"""Scritture su `decisions`: run del motore con il loro esito, e decisioni.

Nessun upsert: ogni run e ogni decisione sono righe nuove. Un run nasce
`running` e viene chiuso con `success`/`partial`/`failed` (migrazione
`0017`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from marketmind_db.audit import RunStatus
from marketmind_db.database import Transaction
from marketmind_db.models.decisions import ModelDecision, ModelRun
from marketmind_llm_decision_engine.llm.schemas import Decision

MAX_ERROR_LENGTH = 2_000


class DecisionRepository:
    def __init__(self, tx: Transaction) -> None:
        self.tx = tx

    def create_run(
        self,
        *,
        portfolio_id: int,
        ts: datetime,
        config: dict[str, Any],
        llm_provider: str,
        model_version: str,
    ) -> int:
        [row] = self.tx.repository(ModelRun).insert_returning(
            [
                {
                    "portfolio_id": portfolio_id,
                    "ts": ts,
                    "config": config,
                    "llm_provider": llm_provider,
                    "model_version": model_version,
                    "status": "running",
                }
            ],
            returning=("run_id",),
        )
        return row["run_id"]

    def finish_run(self, run_id: int, status: RunStatus, error_message: str | None = None) -> None:
        values: dict[str, Any] = {"status": status, "finished_at": datetime.now(timezone.utc)}
        if error_message is not None:
            values["error_message"] = error_message[:MAX_ERROR_LENGTH]
        self.tx.repository(ModelRun).update(values, where={"run_id": run_id})

    def write_decision(
        self,
        *,
        run_id: int,
        asset_id: int,
        ts: datetime,
        decision: Decision,
        context_snapshot: dict[str, Any],
    ) -> int:
        """`context_snapshot` è il contesto esatto passato al modello: audit e
        riproducibilità della decisione."""
        [row] = self.tx.repository(ModelDecision).insert_returning(
            [
                {
                    "run_id": run_id,
                    "asset_id": asset_id,
                    "ts": ts,
                    "decision": decision.decision,
                    "confidence": decision.confidence,
                    "reasoning": decision.reasoning,
                    "size_pct": decision.size_pct,
                    "context_snapshot": context_snapshot,
                }
            ],
            returning=("decision_id",),
        )
        return row["decision_id"]
