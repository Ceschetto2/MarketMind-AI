"""Letture e scritture su `decisions`: run del motore con il loro esito, e
decisioni.

Un run è un ciclo di decisioni di un portfolio: nasce `running` all'apertura
del ciclo, e ogni tentativo (l'apertura e le riprese successive sugli asset
ancora senza decisione) ne aggiorna l'esito `success`/`partial`/`failed`
(migrazione `0017`). Le decisioni sono sempre righe nuove, mai aggiornate.
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
        """Aggiorna l'esito dopo un tentativo; `error_message=None` cancella
        quello di un tentativo precedente (un ciclo completato non resta
        segnato dagli errori di prima)."""
        values: dict[str, Any] = {
            "status": status,
            "finished_at": datetime.now(timezone.utc),
            "error_message": None if error_message is None else error_message[:MAX_ERROR_LENGTH],
        }
        self.tx.repository(ModelRun).update(values, where={"run_id": run_id})

    def latest_run(self, portfolio_id: int) -> ModelRun | None:
        """L'ultimo run del portfolio, cioè il ciclo in corso (un run per
        ciclo: i tentativi successivi aggiungono decisioni allo stesso run)."""
        [run] = self.tx.repository(ModelRun).select(
            where={"portfolio_id": portfolio_id}, order_by=("-ts", "-run_id"), limit=1
        ) or [None]
        return run

    def decided_asset_ids(self, run_id: int) -> set[int]:
        """Gli asset che hanno già una decisione in questo run."""
        return set(
            self.tx.repository(ModelDecision).values("asset_id", where={"run_id": run_id}, distinct=True, limit=None)
        )

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
