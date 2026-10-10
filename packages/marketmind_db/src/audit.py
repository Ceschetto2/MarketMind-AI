"""Audit trail delle esecuzioni di ingestion in `audit.t_ingestion_runs`.

`IngestionRunAudit` scrive la riga del run in transazioni **proprie**,
separate da quelle dei dati: se la scrittura dati fallisce a metà e va in
rollback, il run resta comunque registrato come `failed` con l'errore.
Dentro il blocco il `run_id` è tracciato (`run_context`), così ogni
sessione aperta dalla pipeline collega le righe che scrive a questo run in
`audit.t_audit_logs` via il trigger generico.

Esito finale: `failed` se il blocco solleva un'eccezione, altrimenti quello
dichiarato con `mark_partial()`/`mark_failed()`, altrimenti `success`.
Quale soglia di target falliti renda un run `partial` o `failed` lo decide
la pipeline, non questo modulo.

Un processo che muore senza poter chiudere il proprio run (macchina spenta,
SIGKILL) lo lascia `running` per sempre: all'avvio, `IngestionRunAudit`
chiude come `failed` i run della stessa pipeline (stessi `source` e
`target_table`) rimasti `running` da più di `STALE_AFTER`.
"""

from __future__ import annotations

from contextlib import ExitStack
import logging
from datetime import datetime, timedelta, timezone
from types import TracebackType
from typing import Literal

from marketmind_db.database import Database
from marketmind_db.models.audit import IngestionRun
from marketmind_db.run_context import track_ingestion_run

logger = logging.getLogger(__name__)


RunStatus = Literal["success", "partial", "failed"]

DEFAULT_FAILURE_THRESHOLD = 0.5


def run_outcome(total: int, failed: int, threshold: float = DEFAULT_FAILURE_THRESHOLD) -> RunStatus:
    """Esito di un run fatto di unità di lavoro isolate (target di una
    pipeline, decisioni di un giro del motore): `success` se nessuna è
    fallita, `partial` se ne è fallita almeno una fino a `threshold`
    compreso, `failed` oltre. Condivisa da pipeline e Decision Engine, così
    `t_ingestion_runs` e `t_model_runs` usano la stessa regola."""
    if failed == 0:
        return "success"
    if failed / total > threshold:
        return "failed"
    return "partial"


class IngestionRunAudit:
    MAX_ERROR_LENGTH = 2_000
    # Ben oltre la durata di qualunque pipeline: un run `running` più vecchio
    # di così appartiene a un processo che non c'è più.
    STALE_AFTER = timedelta(hours=6)

    def __init__(self, db: Database, source: str, target_table: str) -> None:
        self.db = db
        self.source = source
        self.target_table = target_table
        self.run_id: int | None = None
        self.rows_written = 0
        self._outcome: tuple[str, str] | None = None
        self._stack = ExitStack()

    def mark_partial(self, message: str) -> None:
        self._outcome = ("partial", message)

    def mark_failed(self, message: str) -> None:
        self._outcome = ("failed", message)

    def __enter__(self) -> IngestionRunAudit:
        now = datetime.now(timezone.utc)
        with self.db.transaction() as tx:
            runs = tx.repository(IngestionRun)
            closed = runs.update(
                {
                    "status": "failed",
                    "finished_at": now,
                    "error_message": "processo terminato senza chiudere il run (chiuso dal run successivo)",
                },
                where=[
                    IngestionRun.source == self.source,
                    IngestionRun.target_table == self.target_table,
                    IngestionRun.status == "running",
                    IngestionRun.started_at < now - self.STALE_AFTER,
                ],
            )
            if closed:
                logger.warning(
                    "%s → %s: %d run orfani chiusi come failed", self.source, self.target_table, closed
                )
            [row] = runs.insert_returning(
                [
                    {
                        "source": self.source,
                        "target_table": self.target_table,
                        "started_at": now,
                        "status": "running",
                    }
                ],
                returning=("run_id",),
            )
        self.run_id = row["run_id"]
        self._stack.enter_context(track_ingestion_run(self.run_id))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self._stack.close()
        if exc is not None:
            status, message = "failed", str(exc) or exc_type.__name__
        elif self._outcome is not None:
            status, message = self._outcome
        else:
            status, message = "success", None

        values = {
            "status": status,
            "finished_at": datetime.now(timezone.utc),
            "rows_written": self.rows_written,
        }
        if message is not None:
            values["error_message"] = message[: self.MAX_ERROR_LENGTH]
        with self.db.transaction() as tx:
            tx.repository(IngestionRun).update(values, where={"run_id": self.run_id})
        return False
