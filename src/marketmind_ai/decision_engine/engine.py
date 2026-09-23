"""Il loop del motore decisionale: un run indipendente per ciascun portfolio
'model' attivo, ciascuno con il proprio provider — mai condividono stato.

Un portfolio non parte con lo scope dell'intero universo: va prima
inizializzato (`initialize_portfolio()`). Il bootstrap non si ferma alla
scelta degli asset da osservare (`select_watchlist()`, non `decide()`): si
conclude con un primo giro di `decide()` sulla watchlist appena scelta,
nello stesso passo — un portfolio appena attivato ha subito un giudizio
(anche HOLD, se il modello preferisce aspettare) su ogni asset che osserva,
non aspetta il prossimo giro dovuto. `run_due_decisions()` gira sulla
stessa watchlist (`get_watchlist`, non più l'universo condiviso); un
portfolio mai inizializzato ha una watchlist vuota e il suo run produce
zero decisioni, non un errore.

Un `BUY`/`SELL` non resta solo un giudizio: viene eseguito subito
(`execute_trade`, `db/portfolio_writer.py`) nella stessa transazione della
`ModelDecision` — cash e posizione si aggiornano nello stesso passo in cui
la decisione si registra, mai in un secondo momento da un Backtesting
Engine. La size del trade (`decision.size_pct`) è proposta dall'LLM
stesso, non una regola deterministica qui dentro. Il prezzo di esecuzione
è l'ultimo noto nel context package (`context.prices[-1]`, mai un dato
futuro); se il context non ha prezzi per l'asset, il trade è saltato con
un warning, non un errore che blocca il run.

Cadenza per-portfolio, non un timer per-portfolio: dopo ogni giro (bootstrap
incluso) il motore schedula da sé quando tornare a girare per quel
portfolio (`t_portfolios.next_decision_at`, `schedule_next_decision()`,
default `DEFAULT_DECISION_INTERVAL`) — un timer condiviso invoca
`run_due_decisions()` a intervalli più fitti del default (`decision_engine/
pipeline.py`, entry point standalone) e questa funzione filtra da sola chi è
davvero scaduto (`get_due_model_portfolios`), non chi è "attivo" in
generale. Il campo esiste apposta perché il motore possa scrivere un
prossimo giro diverso dal default per un singolo portfolio, in futuro anche
in risposta a un rinvio dell'LLM (`Decision.defer`, non ancora collegato).
Dettaglio in `Market Mind AI - Docs/Decision Engine/05_timer_e_cadenza.md`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from marketmind_ai.db.context_reader import get_decision_universe
from marketmind_ai.db.decision_writer import create_model_run, write_model_decision
from marketmind_ai.db.models.market_data import Asset
from marketmind_ai.db.models.portfolio import Portfolio
from marketmind_ai.db.portfolio_reader import (
    get_due_model_portfolios,
    get_portfolio,
    get_watchlist,
)
from marketmind_ai.db.portfolio_writer import execute_trade, schedule_next_decision, write_watchlist
from marketmind_ai.db.session import get_session, track_model_run
from marketmind_ai.decision_engine.context_builder import (
    DEFAULT_COMPANY_EVENT_DAYS_BACK,
    DEFAULT_NEWS_DAYS_BACK,
    DEFAULT_NEWS_MAX_ITEMS,
    DEFAULT_PRICE_DAYS_BACK,
    build_bootstrap_context,
    build_context,
)
from marketmind_ai.llm.base import LLMProvider
from marketmind_ai.llm.exceptions import DecisionError
from marketmind_ai.llm.factory import get_provider

logger = logging.getLogger(__name__)

# Default della cadenza per-portfolio (§ "Cadenza e storico" in CLAUDE.md):
# non un cron fisso, un valore di partenza che il motore scrive su
# `next_decision_at` a ogni giro e potrà un giorno sovrascrivere con un
# intervallo diverso per un singolo portfolio.
DEFAULT_DECISION_INTERVAL = timedelta(days=7)


def initialize_portfolio(portfolio_id: int) -> None:
    """Bootstrap di un portfolio, in due passi nello stesso giro.

    Primo: sceglie lo scope di asset di questo portfolio con un'unica
    chiamata `select_watchlist()` (l'universo intero + la strategia del
    portfolio), poi sostituisce la sua watchlist con la selezione — un
    replace totale, non un merge (`write_watchlist`). Un `symbol`
    restituito dal modello che non corrisponde a nessun asset dell'universo
    viene scartato con un warning, non propagato.

    Secondo: gira subito un primo `_run_for_portfolio()` sulla watchlist
    appena scelta, riusando lo stesso `provider` — un portfolio appena
    attivato non aspetta il prossimo giro dovuto per avere un primo
    giudizio su ciò che osserva. Al termine, schedula anche il prossimo
    giro (`_schedule_next_run`, default `DEFAULT_DECISION_INTERVAL`): un
    portfolio bootstrappato è da subito "non scaduto", non riprocessato dal
    timer condiviso al giro immediatamente successivo.

    Se il bootstrap stesso fallisce (`select_watchlist()` solleva
    `DecisionError`), l'eccezione si propaga: senza uno scope non c'è
    nessun primo round da fare.
    """
    with get_session() as session:
        portfolio = get_portfolio(session, portfolio_id)
        context = build_bootstrap_context(session, portfolio_id)
        universe_by_symbol = {a.symbol: a for a in get_decision_universe(session)}

    provider = get_provider(name=portfolio.llm_provider, model=portfolio.model_version, api_key=None)

    try:
        selection = provider.select_watchlist(context.model_dump(mode="json"))
    except DecisionError:
        logger.exception("bootstrap fallito per il portfolio %s", portfolio_id)
        raise

    resolved_assets: list[Asset] = []
    for symbol in selection.symbols:
        asset = universe_by_symbol.get(symbol)
        if asset is None:
            logger.warning(
                "select_watchlist ha restituito %r, non nell'universo — scartato", symbol
            )
            continue
        resolved_assets.append(asset)

    with get_session() as session:
        write_watchlist(
            session,
            portfolio_id,
            [a.asset_id for a in resolved_assets],
            added_at=datetime.now(timezone.utc),
        )

    logger.info(
        "bootstrap completato per il portfolio %d: %d/%d symbol validi",
        portfolio_id,
        len(resolved_assets),
        len(selection.symbols),
    )

    as_of = datetime.now(timezone.utc)
    _run_for_portfolio(portfolio, resolved_assets, provider, as_of)
    _schedule_next_run(portfolio_id, as_of)


def run_due_decisions(as_of: datetime | None = None) -> None:
    """Un ciclo completo: un run indipendente per ogni portfolio 'model'
    attivo il cui giro è dovuto ORA (`get_due_model_portfolios` — non
    "attivo" in generale, ma "scaduto": `next_decision_at` nullo o
    `<= as_of`), scoperto sulla propria watchlist (`get_watchlist`), non
    sull'intero universo. Un errore isolato a un portfolio — nella
    creazione del provider o in una singola decisione — non blocca gli
    altri portfolio: ciascuno gira nel proprio `try` a sé, e un fallimento
    non fa avanzare `next_decision_at`: il portfolio resta "scaduto" e
    riprovato al prossimo giro del timer condiviso, non perso per una
    settimana.
    """
    as_of = as_of or datetime.now(timezone.utc)

    with get_session() as session:
        portfolios = get_due_model_portfolios(session, as_of)

    for portfolio in portfolios:
        try:
            provider = get_provider(
                name=portfolio.llm_provider, model=portfolio.model_version, api_key=None
            )
            with get_session() as session:
                watchlist = get_watchlist(session, portfolio.portfolio_id)
            _run_for_portfolio(portfolio, watchlist, provider, as_of)
            _schedule_next_run(portfolio.portfolio_id, as_of)
        except Exception:
            logger.exception("run fallito per il portfolio %s, salto", portfolio.portfolio_id)


def _schedule_next_run(portfolio_id: int, as_of: datetime) -> None:
    next_decision_at = as_of + DEFAULT_DECISION_INTERVAL
    with get_session() as session:
        schedule_next_decision(session, portfolio_id, next_decision_at)
    logger.info(
        "portfolio %d: prossimo giro schedulato per %s", portfolio_id, next_decision_at
    )


def _run_for_portfolio(
    portfolio: Portfolio, watchlist: list[Asset], provider: LLMProvider, as_of: datetime
) -> None:
    run_id = _create_run(portfolio, as_of)

    decisions_written = 0
    # track_model_run: ogni get_session() aperta qui dentro imposta da sé
    # marketmind.model_run_id, così i trigger di snapshot del portfolio
    # (db/session.py) collegano cash/posizione a questo run — necessario
    # per il Backtesting Engine, che isola i trade di un run tramite quel
    # collegamento (Market Mind AI - Docs/Backtest/00_motore_backtest.md).
    with track_model_run(run_id):
        for asset in watchlist:
            with get_session() as session:
                context = build_context(
                    session, asset.asset_id, asset.symbol, portfolio_id=portfolio.portfolio_id, as_of=as_of
                )

            try:
                decision = provider.decide(context.model_dump(mode="json"))
            except DecisionError:
                logger.warning(
                    "decisione fallita per %s (portfolio %s), salto",
                    asset.symbol,
                    portfolio.portfolio_id,
                )
                continue

            with get_session() as session:
                write_model_decision(
                    session,
                    run_id=run_id,
                    asset_id=asset.asset_id,
                    ts=as_of,
                    decision=decision,
                    context_snapshot=context.model_dump(mode="json"),
                )
                if decision.decision in ("BUY", "SELL"):
                    if context.prices:
                        execute_trade(
                            session,
                            portfolio.portfolio_id,
                            asset.asset_id,
                            decision,
                            price=context.prices[-1].close,
                        )
                    else:
                        logger.warning(
                            "nessun prezzo disponibile per %s (portfolio %s), trade non eseguito",
                            asset.symbol,
                            portfolio.portfolio_id,
                        )
            decisions_written += 1

    logger.info(
        "run %d (portfolio %d) completato: %d/%d decisioni scritte",
        run_id,
        portfolio.portfolio_id,
        decisions_written,
        len(watchlist),
    )


def _create_run(portfolio: Portfolio, as_of: datetime) -> int:
    # Le finestre sono ancora quelle di default di context_builder — nessuna
    # personalizzazione per-portfolio oggi; registrate comunque per audit,
    # così un futuro run con finestre diverse resta distinguibile a
    # posteriori.
    config = {
        "price_days_back": DEFAULT_PRICE_DAYS_BACK,
        "news_days_back": DEFAULT_NEWS_DAYS_BACK,
        "news_max_items": DEFAULT_NEWS_MAX_ITEMS,
        "company_event_days_back": DEFAULT_COMPANY_EVENT_DAYS_BACK,
    }
    with get_session() as session:
        return create_model_run(
            session,
            portfolio_id=portfolio.portfolio_id,
            ts=as_of,
            config=config,
            llm_provider=portfolio.llm_provider,
            model_version=portfolio.model_version,
        )
