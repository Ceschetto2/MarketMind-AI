"""Il loop del motore decisionale: per ogni asset dell'universo (esclusi i
benchmark), costruisce il context package e chiama il provider LLM.

Non ancora un entry point standalone (nessun `if __name__ == "__main__":`,
nessun Quadlet/timer): il wiring per la cadenza settimanale è deliberatamente
rimandato, `run_weekly_decisions()` resta per ora solo una funzione
richiamabile. `provider`/`llm_provider_name`/`model_version` restano
parametri espliciti — nessuna istanza di `GeminiProvider` creata qui dentro
— così il chiamante decide quale provider usare, coerente con `llm/base.py`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from marketmind_ai.db.context_reader import get_decision_universe
from marketmind_ai.db.decision_writer import create_model_run, write_model_decision
from marketmind_ai.db.session import get_session
from marketmind_ai.decision_engine.context_builder import (
    DEFAULT_COMPANY_EVENT_DAYS_BACK,
    DEFAULT_NEWS_DAYS_BACK,
    DEFAULT_NEWS_MAX_ITEMS,
    DEFAULT_PRICE_DAYS_BACK,
    build_context,
)
from marketmind_ai.llm.base import LLMProvider
from marketmind_ai.llm.exceptions import DecisionError

logger = logging.getLogger(__name__)


def run_weekly_decisions(
    provider: LLMProvider,
    llm_provider_name: str,
    model_version: str,
    as_of: datetime | None = None,
) -> None:
    """Un run completo: una `Decision` per ogni asset dell'universo
    decisionale (esclusi i benchmark, `get_decision_universe`).

    Un `DecisionError` per un singolo asset non interrompe il run: viene
    loggato e quell'asset resta senza una riga in `t_model_decisions` per
    questo run — un buco esplicito nello storico, mai una decisione
    inventata (coerente con `llm/exceptions.py`).
    """
    as_of = as_of or datetime.now(timezone.utc)

    with get_session() as session:
        universe = get_decision_universe(session)

    run_id = _create_run(as_of, llm_provider_name, model_version)

    decisions_written = 0
    for asset in universe:
        with get_session() as session:
            context = build_context(session, asset.asset_id, asset.symbol, as_of=as_of)

        try:
            decision = provider.decide(context.model_dump(mode="json"))
        except DecisionError:
            logger.warning("decisione fallita per %s, salto", asset.symbol)
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
        "run %d completato: %d/%d decisioni scritte", run_id, decisions_written, len(universe)
    )


def _create_run(as_of: datetime, llm_provider_name: str, model_version: str) -> int:
    # Le finestre sono ancora quelle di default di context_builder — nessuna
    # personalizzazione per-run oggi; registrate comunque per audit, così un
    # futuro run con finestre diverse resta distinguibile a posteriori.
    config = {
        "price_days_back": DEFAULT_PRICE_DAYS_BACK,
        "news_days_back": DEFAULT_NEWS_DAYS_BACK,
        "news_max_items": DEFAULT_NEWS_MAX_ITEMS,
        "company_event_days_back": DEFAULT_COMPANY_EVENT_DAYS_BACK,
    }
    with get_session() as session:
        return create_model_run(
            session,
            ts=as_of,
            config=config,
            llm_provider=llm_provider_name,
            model_version=model_version,
        )
