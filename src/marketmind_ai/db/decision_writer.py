"""Strato di scrittura verso lo schema `decisions`, per `decision_engine/`.

Distinto da `db/writer.py` (scritture idempotenti di ingestion su
`market_data`/`raw`): qui non c'è upsert — ogni run e ogni decisione sono
righe nuove, mai aggiornate. Coerente col principio "solo `db/` parla con
Postgres" (`Market Mind AI - Docs/Architettura/00_struttura_cartelle.md`).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from marketmind_ai.db.models.decisions import ModelDecision, ModelRun
from marketmind_ai.llm.schemas import Decision


def create_model_run(
    session: Session,
    portfolio_id: int,
    ts: datetime,
    config: dict,
    llm_provider: str,
    model_version: str,
) -> int:
    """Registra una esecuzione del motore decisionale per un portfolio (un
    run appartiene a un solo portfolio, mai più un run universale
    sull'intero universo condiviso tra portfolio). `config` è la
    configurazione con cui è girato il run (es. le finestre del context
    builder), utile per audit e riproducibilità."""
    run = ModelRun(
        portfolio_id=portfolio_id,
        ts=ts,
        config=config,
        llm_provider=llm_provider,
        model_version=model_version,
    )
    session.add(run)
    session.flush()
    return run.run_id


def write_model_decision(
    session: Session,
    run_id: int,
    asset_id: int,
    ts: datetime,
    decision: Decision,
    context_snapshot: dict,
) -> int:
    """Persiste una `Decision` prodotta da un `LLMProvider` per un asset in
    un run. `context_snapshot` è il `model_dump()` esatto del
    `DecisionContext` passato al modello — audit e riproducibilità, non le
    righe grezze di `market_data`."""
    row = ModelDecision(
        run_id=run_id,
        asset_id=asset_id,
        ts=ts,
        decision=decision.decision,
        confidence=decision.confidence,
        reasoning=decision.reasoning,
        size_pct=decision.size_pct,
        context_snapshot=context_snapshot,
    )
    session.add(row)
    session.flush()
    return row.decision_id
