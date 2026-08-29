"""Tabelle dello schema `audit` — logging e auditing operativo.

Rispecchia colonna per colonna l'`erDiagram` confermato in
`Market Mind AI - Docs/Architettura/01_schema_dati_er.md`. `t_ingestion_runs`
traccia ogni esecuzione di uno script di ingestion — utile in un'architettura
senza orchestratore dedicato, dove altrimenti l'unica visibilità sarebbe
`journalctl`. `t_audit_logs` è un log generico di modifiche riga per riga
sulle tabelle di `market_data`, popolato da un trigger Postgres (vedi
`alembic/versions/0001_initial_schema.py`, non dallo strato applicativo, per
non dipendere dal ricordarsi di loggare in ogni nuovo path di scrittura):
`row_pk` serializza la chiave della riga interessata come testo (non una FK
tipizzata, per restare generico su tabelle con PK di forma diversa —
singola, come `asset_id`, o composita, come `(asset_id, ts, source)` o
`(indicator, ts)`), e `diff` porta i valori prima/dopo. Il modello ORM qui
sotto serve a leggere `t_audit_logs`, non a scriverla.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from marketmind_ai.db.base import Base

SCHEMA = "audit"


class IngestionRun(Base):
    """Una esecuzione di uno script di ingestion verso una delle tabelle `market_data`."""

    __tablename__ = "t_ingestion_runs"

    run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    target_table: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    rows_written: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "source IN ('yfinance', 'gdelt', 'finnhub', 'fred', 'fmp')", name="source"
        ),
        CheckConstraint(
            "status IN ('running', 'success', 'failed', 'partial')", name="status"
        ),
        Index(
            "ib_ingestion_runs_source_started", "source", text("started_at DESC")
        ),
        Index(
            "ib_ingestion_runs_running",
            "status",
            postgresql_where=text("status = 'running'"),
        ),
        {"schema": SCHEMA},
    )


class AuditLog(Base):
    """Riga di audit scritta dal trigger `audit.fn_audit_log()` — sola lettura da ORM."""

    __tablename__ = "t_audit_logs"

    audit_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.t_ingestion_runs.run_id")
    )
    schema_name: Mapped[str] = mapped_column(String(50), nullable=False)
    table_name: Mapped[str] = mapped_column(String(100), nullable=False)
    row_pk: Mapped[str] = mapped_column(Text, nullable=False)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    diff: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint(
            "operation IN ('INSERT', 'UPDATE', 'DELETE')", name="operation"
        ),
        Index("ib_audit_logs_table_row", "schema_name", "table_name", "row_pk"),
        Index("ib_audit_logs_run_id", "run_id"),
        Index(
            "ir_audit_logs_changed_at",
            "changed_at",
            postgresql_using="brin",
        ),
        {"schema": SCHEMA},
    )
