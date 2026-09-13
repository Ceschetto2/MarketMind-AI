"""Il loop del motore decisionale: un run indipendente per ciascun portfolio
'model' attivo, ciascuno con il proprio provider — mai condividono stato.

Non ancora un entry point standalone (nessun `if __name__ == "__main__":`,
nessun Quadlet/timer): il wiring per la cadenza settimanale è deliberatamente
rimandato, `run_weekly_decisions()` resta per ora solo una funzione
richiamabile.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from marketmind_ai.db.context_reader import get_decision_universe
from marketmind_ai.db.decision_writer import create_model_run, write_model_decision
from marketmind_ai.db.models.portfolio import Portfolio
from marketmind_ai.db.portfolio_reader import get_active_model_portfolios
from marketmind_ai.db.session import get_session
from marketmind_ai.decision_engine.context_builder import (
    DEFAULT_COMPANY_EVENT_DAYS_BACK,
    DEFAULT_NEWS_DAYS_BACK,
    DEFAULT_NEWS_MAX_ITEMS,
    DEFAULT_PRICE_DAYS_BACK,
    build_context,
)
from marketmind_ai.llm.exceptions import DecisionError
from marketmind_ai.llm.factory import get_provider

logger = logging.getLogger(__name__)


def run_weekly_decisions(as_of: datetime | None = None) -> None:
    """Un ciclo completo: un run indipendente per ogni portfolio 'model'
    attivo (`get_active_model_portfolios`, esclude benchmark e portfolio
    sospesi). Un errore isolato a un portfolio — nella creazione del
    provider o in una singola decisione — non blocca gli altri portfolio:
    ciascuno gira nel proprio `try` a sé.
    """
    as_of = as_of or datetime.now(timezone.utc)

    with get_session() as session:
        portfolios = get_active_model_portfolios(session)
        universe = get_decision_universe(session)

    for portfolio in portfolios:
        try:
            _run_for_portfolio(portfolio, universe, as_of)
        except Exception:
            logger.exception("run fallito per il portfolio %s, salto", portfolio.portfolio_id)


def _run_for_portfolio(portfolio: Portfolio, universe: list, as_of: datetime) -> None:
    provider = get_provider(name=portfolio.llm_provider, model=portfolio.model_version, api_key=None)
    run_id = _create_run(portfolio, as_of)

    decisions_written = 0
    for asset in universe:
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
        decisions_written += 1

    logger.info(
        "run %d (portfolio %d) completato: %d/%d decisioni scritte",
        run_id,
        portfolio.portfolio_id,
        decisions_written,
        len(universe),
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
