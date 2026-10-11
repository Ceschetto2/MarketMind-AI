"""Coda dei job chiesti dalla dashboard: pipeline di ingestion, giro del
Decision Engine, bootstrap e backtest di un portfolio.

La dashboard non esegue nulla in proprio e non comanda systemd (nel suo
container non c'è): scrive una richiesta in `ops.t_job_requests`, e un
dispatcher sull'host la prende in carico e avvia la unit corrispondente, con
gli stessi container, ruoli Postgres e secret dei timer
(`marketmind_frontend.dispatch`, `deploy/systemd/marketmind_frontend/`).
L'esito del job si legge dove lo scrive il job stesso (`t_ingestion_runs`,
`t_model_runs`, `t_backtest_results`).

Una richiesta uguale già pendente non si duplica. Al momento della presa in
carico, le richieste pendenti da più di `REQUEST_TTL` scadono (`failed`):
un dispatcher rimasto fermo non deve, alla ripartenza, avviare una raffica
di job chiesti ore prima.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from marketmind_db.database import Transaction
from marketmind_db.models.ops import JobRequest as JobRequestRow

# Le pipeline con una unit `marketmind-ingest-<nome>`. Il container della
# dashboard non può interrogare systemd: un test le confronta con le unit di
# `deploy/quadlet/marketmind_pipelines/`.
PIPELINES = (
    "finnhub-earnings",
    "finnhub-news",
    "fmp",
    "fred",
    "gdelt-ngrams",
    "universe-csv",
    "yfinance-assets",
    "yfinance-prices",
)
JOB_KINDS = ("pipeline", "decide", "init-portfolio", "backtest")
REQUEST_TTL = timedelta(hours=1)

_PORTFOLIO_ID = re.compile(r"^[1-9][0-9]{0,18}$")


class InvalidJobError(ValueError):
    """Tipo di job sconosciuto o target non valido per quel tipo."""


def normalize_job(kind: str, target: object = None) -> tuple[str, str | None]:
    """Valida tipo e target e li porta nella forma salvata (target testo).
    Nessun valore arbitrario arriva al dispatcher, che ne ricava un nome di
    unit systemd."""
    if kind == "decide":
        if target is not None:
            raise InvalidJobError("il giro del Decision Engine non ha un target")
        return kind, None
    if kind == "pipeline":
        if target not in PIPELINES:
            raise InvalidJobError(f"pipeline sconosciuta: {target!r}")
        return kind, str(target)
    if kind in ("init-portfolio", "backtest"):
        text = str(target) if target is not None else ""
        if isinstance(target, bool) or not _PORTFOLIO_ID.match(text):
            raise InvalidJobError(f"portfolio_id non valido: {target!r}")
        return kind, text
    raise InvalidJobError(f"tipo di job sconosciuto: {kind!r}")


@dataclass(frozen=True)
class JobRequest:
    job_request_id: int
    kind: str
    target: str | None
    requested_at: datetime | None
    status: str
    started_at: datetime | None
    error_message: str | None

    @classmethod
    def of(cls, row: JobRequestRow) -> JobRequest:
        return cls(row.job_request_id, row.kind, row.target, row.requested_at, row.status, row.started_at,
                   row.error_message)

    @property
    def label(self) -> str:
        return {
            "pipeline": f"pipeline {self.target}",
            "decide": "giro del Decision Engine",
            "init-portfolio": f"bootstrap del portfolio {self.target}",
            "backtest": f"backtest del portfolio {self.target}",
        }.get(self.kind, self.kind)


class JobQueue:
    def __init__(self, tx: Transaction) -> None:
        self._requests = tx.repository(JobRequestRow)

    def request(self, kind: str, target: object = None, *, now: datetime) -> tuple[JobRequest, bool]:
        """Accoda un job. Restituisce la richiesta e se è nuova: una richiesta
        uguale ancora pendente viene restituita invece di duplicarla."""
        kind, target = normalize_job(kind, target)
        pending = self._requests.select(
            where={"kind": kind, "target": target, "status": "pending"}, order_by=("job_request_id",), limit=1
        )
        if pending:
            return JobRequest.of(pending[0]), False
        [row] = self._requests.insert_returning(
            [{"kind": kind, "target": target, "status": "pending", "requested_at": now}],
            returning=("job_request_id",),
        )
        return JobRequest(row["job_request_id"], kind, target, now, "pending", None, None), True

    def claim(self, *, now: datetime) -> JobRequest | None:
        """Prende in carico la richiesta pendente più vecchia (`started`), dopo
        aver fatto scadere quelle più vecchie di `REQUEST_TTL`. Un solo
        dispatcher alla volta (la sua unit è `oneshot`): l'UPDATE condizionato
        allo stato basta a non prendere due volte la stessa richiesta."""
        self._requests.update(
            {"status": "failed",
             "error_message": f"scaduta: non presa in carico entro {REQUEST_TTL.total_seconds() / 60:.0f} minuti"},
            where=[JobRequestRow.status == "pending", JobRequestRow.requested_at < now - REQUEST_TTL],
        )
        candidates = self._requests.select(
            where={"status": "pending"}, order_by=("requested_at", "job_request_id"), limit=1
        )
        if not candidates:
            return None
        row = candidates[0]
        taken = self._requests.update(
            {"status": "started", "started_at": now},
            where={"job_request_id": row.job_request_id, "status": "pending"},
        )
        if taken != 1:
            return None
        return JobRequest(row.job_request_id, row.kind, row.target, row.requested_at, "started", now, None)

    def fail(self, request_id: int, message: str, *, now: datetime) -> None:
        """L'avvio della unit è fallito: la richiesta resta `failed` col motivo."""
        self._requests.update(
            {"status": "failed", "error_message": message[:2_000], "started_at": now},
            where={"job_request_id": request_id},
        )

    def recent(self, limit: int = 50) -> list[JobRequest]:
        rows = self._requests.select(order_by=("-job_request_id",), limit=limit)
        return [JobRequest.of(r) for r in rows]

    def latest_for(self, kind: str, target: object = None) -> JobRequest | None:
        kind, target = normalize_job(kind, target)
        rows = self._requests.select(where={"kind": kind, "target": target}, order_by=("-job_request_id",), limit=1)
        return JobRequest.of(rows[0]) if rows else None
