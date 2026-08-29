"""Tabelle dello schema `audit` — logging e auditing operativo.

`t_audit_logs` è popolata da un trigger Postgres generico (definito nella
migrazione Alembic, non nello strato applicativo — vedi
`alembic/versions/0001_initial_schema.py`), agganciato alle tabelle a basso
volume/rilevanza operativa (`t_assets`, le tabelle di `decisions`,
`t_ingestion_runs`). Le tabelle di ingestion ad alto volume
(`t_market_prices`, `t_news_events`, `t_macro_events`, `t_company_events`)
non sono audited riga per riga: la provenienza è già in
`source`/`fetched_at`/`raw_payload` e duplicare ogni riga in
`t_audit_logs` moltiplicherebbe inutilmente il volume dati. Il modello ORM
qui sotto serve solo a leggere `t_audit_logs`, non a scriverla.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from marketmind_ai.db.base import Base

SCHEMA = "audit"


class IngestionRun(Base):
    """Una esecuzione di uno script di ingestion verso una delle tabelle `market_data`."""

    __tablename__ = "t_ingestion_runs"

    ingestion_run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    target_table: Mapped[str] = mapped_column(String(100), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="running")
    records_ingested: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'failed')",
            name="status",
        ),
        {"schema": SCHEMA},
    )


class AuditLog(Base):
    """Riga di audit scritta dal trigger `audit.fn_audit_log()` — sola lettura da ORM."""

    __tablename__ = "t_audit_logs"

    audit_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    table_schema: Mapped[str] = mapped_column(String(50), nullable=False)
    table_name: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)
    old_data: Mapped[dict | None] = mapped_column(JSONB)
    new_data: Mapped[dict | None] = mapped_column(JSONB)
    changed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()"
    )

    __table_args__ = (
        CheckConstraint(
            "operation IN ('INSERT', 'UPDATE', 'DELETE')",
            name="operation",
        ),
        {"schema": SCHEMA},
    )
