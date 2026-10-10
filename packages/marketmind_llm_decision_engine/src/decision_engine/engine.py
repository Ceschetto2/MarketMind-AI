"""Il loop del motore decisionale, a cicli: per ciascun portfolio `model`
attivo, indipendente dagli altri e col proprio provider LLM.

Un ciclo è un solo run (`t_model_runs`), il cui `ts` è l'inizio del ciclo.
Quando il portfolio è dovuto (`next_decision_at` nullo o scaduto) si apre un
ciclo: si crea il run, si fissa subito il prossimo ciclo a `ts` +
`interval` (prima di decidere: se il processo muore a metà, lo scatto
successivo riprende questo ciclo invece di aprirne un altro) e si chiede a
`decide()` un giudizio su tutta la watchlist. A ogni scatto successivo,
dentro il ciclo, si richiama il modello solo per gli asset della watchlist
che non hanno ancora una decisione in quel run, aggiungendo le decisioni
allo stesso run: le decisioni già prese, e i loro trade, non si ripetono.
Al ciclo successivo si riparte da tutta la watchlist.

L'esito del run si aggiorna a ogni tentativo, sugli asset ancora senza
decisione rispetto all'intera watchlist (`run_outcome`, la stessa regola
delle pipeline): `success` se non ne manca nessuno, `partial` fino al 50%,
`failed` oltre o per un errore fuori dalle singole decisioni. Serve a
sapere com'è andato il ciclo, non decide la cadenza.

Un BUY/SELL viene eseguito subito, nella stessa transazione della decisione,
all'ultimo prezzo noto nel contesto (mai un dato futuro) se non è più vecchio
di `max_price_age`; altrimenti la decisione resta registrata ma il trade è
saltato. La size è quella proposta dall'LLM. Le decisioni sono sequenziali:
il cash disponibile per un asset tiene conto dei trade già fatti.

Un portfolio va prima inizializzato (`initialize_portfolio`): sceglie la
watchlist con `select_watchlist()` (un replace totale) e apre subito un
ciclo.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from marketmind_db.audit import DEFAULT_FAILURE_THRESHOLD, RunStatus, run_outcome
from marketmind_llm_decision_engine.decision_engine.schemas import DecisionContext
from marketmind_llm_decision_engine.decision_engine.store import DecisionStore, TradeRequest
from marketmind_llm_decision_engine.llm.base import LLMProvider
from marketmind_llm_decision_engine.repositories.market_context import AssetRef
from marketmind_llm_decision_engine.repositories.portfolio import PortfolioRef

logger = logging.getLogger(__name__)

DEFAULT_DECISION_INTERVAL = timedelta(days=7)
# Un trade non si esegue su una barra più vecchia di così: copre il weekend
# (venerdì sera → lunedì mattina), non un'ingestion ferma da giorni.
DEFAULT_MAX_PRICE_AGE = timedelta(days=3)
MAX_REPORTED_FAILURES = 20

ProviderFactory = Callable[[str, str], LLMProvider]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PortfolioRunResult:
    portfolio_id: int
    run_id: int | None
    status: RunStatus
    decisions_total: int
    decisions_written: int = 0
    trades_executed: int = 0
    failures: list[str] = field(default_factory=list)
    # "ciclo" (apertura di un ciclo) o "ripresa" (asset rimasti del ciclo in corso)
    kind: str = "ciclo"


class DecisionEngine:
    def __init__(
        self,
        store: DecisionStore,
        provider_factory: ProviderFactory,
        *,
        clock: Callable[[], datetime] = _utcnow,
        interval: timedelta = DEFAULT_DECISION_INTERVAL,
        max_price_age: timedelta = DEFAULT_MAX_PRICE_AGE,
        failure_threshold: float = DEFAULT_FAILURE_THRESHOLD,
    ) -> None:
        self.store = store
        self.provider_factory = provider_factory
        self.clock = clock
        self.interval = interval
        self.max_price_age = max_price_age
        self.failure_threshold = failure_threshold

    # --- punti d'ingresso ---------------------------------------------------

    def run_due(self, as_of: datetime | None = None) -> list[PortfolioRunResult]:
        """Per ogni portfolio attivo: apre un ciclo se è dovuto, altrimenti
        riprende gli asset ancora senza decisione nel ciclo in corso, altrimenti
        non fa nulla. Un errore su un portfolio non blocca gli altri."""
        as_of = as_of or self.clock()
        results = []
        for portfolio in self.store.active_model_portfolios():
            due = portfolio.next_decision_at is None or portfolio.next_decision_at <= as_of
            try:
                watchlist = self.store.watchlist(portfolio.portfolio_id)
                run_id = None
                targets = watchlist
                if not due:
                    run = self.store.latest_run(portfolio.portfolio_id)
                    if run is None:
                        continue
                    decided = self.store.decided_asset_ids(run.run_id)
                    targets = [a for a in watchlist if a.asset_id not in decided]
                    if not targets:
                        continue
                    run_id = run.run_id
                provider = self._provider(portfolio)
            except Exception as exc:
                logger.exception("portfolio %s: preparazione del giro fallita", portfolio.name)
                results.append(
                    PortfolioRunResult(
                        portfolio.portfolio_id, None, "failed", 0,
                        failures=[f"preparazione: {exc}"], kind="ciclo" if due else "ripresa",
                    )
                )
                continue
            results.append(self._attempt(portfolio, watchlist, targets, provider, as_of, run_id=run_id))
        if not results:
            logger.info("niente da fare a %s: nessun ciclo dovuto né asset da riprendere", as_of.isoformat())
        return results

    def initialize_portfolio(self, portfolio_id: int) -> PortfolioRunResult:
        """Bootstrap: sceglie la watchlist con `select_watchlist()` e apre subito
        un ciclo con lo stesso provider. Un `symbol` fuori dall'universo viene
        scartato con un warning. Se la scelta della watchlist fallisce
        l'eccezione si propaga: senza scope non c'è ciclo."""
        portfolio = self.store.portfolio(portfolio_id)
        provider = self._provider(portfolio)
        context = self.store.bootstrap_context(portfolio_id)
        selection = provider.select_watchlist(context.model_dump(mode="json"))

        universe = {a.symbol: a for a in self.store.decision_universe()}
        unknown = [s for s in selection.symbols if s not in universe]
        if unknown:
            logger.warning("select_watchlist: symbol fuori universo scartati: %s", ", ".join(unknown))
        watchlist = list({s: universe[s] for s in selection.symbols if s in universe}.values())

        as_of = self.clock()
        self.store.replace_watchlist(portfolio_id, [a.asset_id for a in watchlist], added_at=as_of)
        logger.info(
            "portfolio %s: watchlist di %d asset (%d proposti)", portfolio.name, len(watchlist), len(selection.symbols)
        )
        return self._attempt(portfolio, watchlist, watchlist, provider, as_of)

    # --- un tentativo ---------------------------------------------------------

    def _attempt(
        self,
        portfolio: PortfolioRef,
        watchlist: list[AssetRef],
        targets: list[AssetRef],
        provider: LLMProvider,
        as_of: datetime,
        *,
        run_id: int | None = None,
    ) -> PortfolioRunResult:
        """Apertura di un ciclo (`run_id=None`: nuovo run, prossimo ciclo
        fissato subito) o ripresa del ciclo in corso (`run_id` del suo run):
        decide sugli asset `targets` e aggiorna l'esito del run."""
        kind = "ciclo" if run_id is None else "ripresa"
        result = PortfolioRunResult(portfolio.portfolio_id, run_id, "failed", len(watchlist), kind=kind)
        next_cycle = None
        try:
            if result.run_id is None:
                result.run_id = self.store.start_run(portfolio, as_of=as_of)
                next_cycle = as_of + self.interval
                self.store.schedule_next_decision(portfolio.portfolio_id, next_cycle)
            for asset in targets:
                try:
                    if self._decide_asset(portfolio, asset, provider, as_of, result.run_id):
                        result.trades_executed += 1
                    result.decisions_written += 1
                except Exception as exc:
                    logger.warning("portfolio %s, %s: decisione fallita: %s", portfolio.name, asset.symbol, exc)
                    result.failures.append(f"{asset.symbol}: {type(exc).__name__}: {exc}")
            # Gli asset fuori da `targets` hanno già una decisione nel run:
            # quelli ancora senza sono esattamente i falliti di questo tentativo.
            result.status = run_outcome(len(watchlist), len(result.failures), self.failure_threshold)
            message = self._failure_summary(result) if result.failures else None
        except Exception as exc:
            logger.exception("portfolio %s: giro interrotto", portfolio.name)
            result.status = "failed"
            message = f"giro interrotto: {type(exc).__name__}: {exc}"
        except BaseException as exc:
            # SIGTERM o simili: il run si chiude `failed` (verrà ripreso al
            # prossimo scatto), poi l'interruzione si propaga.
            if result.run_id is not None:
                self.store.finish_run(result.run_id, "failed", f"interrotto: {type(exc).__name__}: {exc}")
            raise

        if result.run_id is not None:
            self.store.finish_run(result.run_id, result.status, message)
        logger.info(
            "portfolio %s, run %s (%s): %s, %d/%d decisioni in questo tentativo, %d asset senza decisione, %d trade%s",
            portfolio.name, result.run_id, kind, result.status, result.decisions_written, len(targets),
            len(result.failures), result.trades_executed,
            f"; prossimo ciclo {next_cycle.isoformat()}" if next_cycle else "",
        )
        return result
    def _decide_asset(
        self, portfolio: PortfolioRef, asset: AssetRef, provider: LLMProvider, as_of: datetime, run_id: int
    ) -> bool:
        context = self.store.decision_context(asset, portfolio_id=portfolio.portfolio_id, as_of=as_of)
        payload = context.model_dump(mode="json")
        decision = provider.decide(payload)
        trade = self._trade_request(portfolio, asset, decision.decision, decision.size_pct, context, as_of)
        return self.store.record_decision(
            run_id=run_id,
            portfolio_id=portfolio.portfolio_id,
            asset=asset,
            as_of=as_of,
            decision=decision,
            context=payload,
            trade=trade,
        )

    def _trade_request(
        self,
        portfolio: PortfolioRef,
        asset: AssetRef,
        action: str,
        size_pct: float | None,
        context: DecisionContext,
        as_of: datetime,
    ) -> TradeRequest | None:
        if action not in ("BUY", "SELL"):
            return None
        if not context.prices:
            logger.warning("portfolio %s, %s: nessun prezzo nel contesto, trade %s saltato", portfolio.name, asset.symbol, action)
            return None
        last = context.prices[-1]
        age = as_of - last.ts
        if age > self.max_price_age:
            logger.warning(
                "portfolio %s, %s: ultimo prezzo vecchio di %s (oltre %s), trade %s saltato",
                portfolio.name, asset.symbol, age, self.max_price_age, action,
            )
            return None
        return TradeRequest(action=action, size_pct=size_pct, price=last.close)

    def _provider(self, portfolio: PortfolioRef) -> LLMProvider:
        if not portfolio.llm_provider or not portfolio.model_version:
            raise ValueError(f"portfolio {portfolio.name}: llm_provider/model_version non impostati")
        return self.provider_factory(portfolio.llm_provider, portfolio.model_version)

    @staticmethod
    def _failure_summary(result: PortfolioRunResult) -> str:
        shown = result.failures[:MAX_REPORTED_FAILURES]
        more = len(result.failures) - len(shown)
        suffix = f"; ... e altre {more}" if more else ""
        return f"{len(result.failures)}/{result.decisions_total} asset senza decisione: " + "; ".join(shown) + suffix
