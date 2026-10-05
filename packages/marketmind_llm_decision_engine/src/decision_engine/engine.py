"""Il loop del motore decisionale: un giro indipendente per ciascun
portfolio `model` attivo il cui turno è dovuto, ciascuno col proprio
provider LLM — i portfolio non condividono mai stato.

Un giro (`_run_portfolio`) chiede a `decide()` un giudizio BUY/SELL/HOLD per
ogni asset della watchlist del portfolio e lo registra; un BUY/SELL viene
eseguito subito, nella stessa transazione della decisione, all'ultimo
prezzo noto nel contesto (mai un dato futuro), se non è più vecchio di
`max_price_age` — altrimenti la decisione resta registrata ma il trade è
saltato. La size è quella proposta dall'LLM (`Decision.size_pct`). Le
decisioni di un giro sono sequenziali: il cash disponibile per un asset
tiene conto dei trade già fatti sugli asset precedenti.

L'errore su un asset è isolato e il giro prosegue. L'esito del giro è
quello delle pipeline (`run_outcome`): `success`, `partial` (almeno una
decisione fallita, fino al 50%), `failed` (oltre, o errore fuori dalle
singole decisioni). Il prossimo giro è schedulato (`next_decision_at`,
default +7 giorni) solo con `success`/`partial`: un giro `failed` lascia il
portfolio dovuto, riprovato al prossimo scatto del timer orario, invece di
fargli perdere una settimana in silenzio.

Un portfolio va prima inizializzato (`initialize_portfolio`): sceglie la
watchlist con `select_watchlist()` (un replace totale) e fa subito un primo
giro di decisioni, così un portfolio appena attivato ha un giudizio su
ogni asset che osserva.
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
        """Un giro per ogni portfolio dovuto ora; un errore su un portfolio non
        blocca gli altri."""
        as_of = as_of or self.clock()
        portfolios = self.store.due_portfolios(as_of)
        if not portfolios:
            logger.info("nessun portfolio dovuto a %s", as_of.isoformat())
            return []
        logger.info("%d portfolio dovuti: %s", len(portfolios), ", ".join(p.name for p in portfolios))

        results = []
        for portfolio in portfolios:
            try:
                provider = self._provider(portfolio)
                watchlist = self.store.watchlist(portfolio.portfolio_id)
            except Exception as exc:
                logger.exception("portfolio %s: preparazione del giro fallita", portfolio.name)
                results.append(
                    PortfolioRunResult(portfolio.portfolio_id, None, "failed", 0, failures=[f"preparazione: {exc}"])
                )
                continue
            results.append(self._run_portfolio(portfolio, watchlist, provider, as_of))
        return results

    def initialize_portfolio(self, portfolio_id: int) -> PortfolioRunResult:
        """Bootstrap: sceglie la watchlist con `select_watchlist()` e fa subito
        un primo giro di decisioni con lo stesso provider. Un `symbol` fuori
        dall'universo viene scartato con un warning. Se la scelta della
        watchlist fallisce l'eccezione si propaga: senza scope non c'è giro."""
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
        return self._run_portfolio(portfolio, watchlist, provider, as_of)

    # --- un giro --------------------------------------------------------------

    def _run_portfolio(
        self, portfolio: PortfolioRef, watchlist: list[AssetRef], provider: LLMProvider, as_of: datetime
    ) -> PortfolioRunResult:
        result = PortfolioRunResult(portfolio.portfolio_id, None, "failed", len(watchlist))
        try:
            result.run_id = self.store.start_run(portfolio, as_of=as_of)
            for asset in watchlist:
                try:
                    if self._decide_asset(portfolio, asset, provider, as_of, result.run_id):
                        result.trades_executed += 1
                    result.decisions_written += 1
                except Exception as exc:
                    logger.warning("portfolio %s, %s: decisione fallita: %s", portfolio.name, asset.symbol, exc)
                    result.failures.append(f"{asset.symbol}: {type(exc).__name__}: {exc}")
            result.status = run_outcome(len(watchlist), len(result.failures), self.failure_threshold)
            message = self._failure_summary(result) if result.failures else None
        except Exception as exc:
            logger.exception("portfolio %s: giro interrotto", portfolio.name)
            result.status = "failed"
            message = f"giro interrotto: {type(exc).__name__}: {exc}"
        except BaseException as exc:
            # SIGTERM o simili: il run si chiude `failed`, poi l'interruzione
            # si propaga (nessun altro portfolio deve partire).
            if result.run_id is not None:
                self.store.finish_run(result.run_id, "failed", f"interrotto: {type(exc).__name__}: {exc}")
            raise

        if result.run_id is not None:
            self.store.finish_run(result.run_id, result.status, message)
        if result.status in ("success", "partial"):
            next_at = as_of + self.interval
            self.store.schedule_next_decision(portfolio.portfolio_id, next_at)
            schedule = f"prossimo giro {next_at.isoformat()}"
        else:
            schedule = "prossimo giro non schedulato: riprovato al prossimo scatto del timer"
        logger.info(
            "portfolio %s, run %s: %s, %d/%d decisioni, %d trade; %s",
            portfolio.name, result.run_id, result.status, result.decisions_written,
            result.decisions_total, result.trades_executed, schedule,
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
        return f"{len(result.failures)}/{result.decisions_total} decisioni fallite: " + "; ".join(shown) + suffix
