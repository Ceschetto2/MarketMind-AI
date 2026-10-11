"""Tabelle dello schema `ops` — richieste operative.

`t_job_requests` è la coda dei job chiesti dalla dashboard (una pipeline, un
giro del Decision Engine, il bootstrap o il backtest di un portfolio): la
dashboard, anche in un container senza accesso a systemd, scrive qui una
richiesta, e un dispatcher sull'host la prende in carico e avvia la unit
corrispondente (`marketmind_frontend.dispatch`). Uno schema a sé perché non
è né un dato di mercato né una decisione: è uno scambio tra l'interfaccia e
chi esegue i job.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Index, String, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from marketmind_db.base import Base

SCHEMA = "ops"


class JobRequest(Base):
    """Una richiesta di job: `pending` finché il dispatcher non la prende in
    carico (`started`, la unit è stata avviata) o non fallisce l'avvio o
    scade (`failed`). L'esito del job stesso non sta qui ma dove lo scrive il
    job (`t_ingestion_runs`, `t_model_runs`, `t_backtest_results`)."""

    __tablename__ = "t_job_requests"

    job_request_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    target: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'pending'"))
    requested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("kind IN ('pipeline', 'decide', 'init-portfolio', 'backtest')", name="kind"),
        CheckConstraint("status IN ('pending', 'started', 'failed')", name="status"),
        # Solo il giro del Decision Engine non ha un target.
        CheckConstraint("(kind = 'decide') = (target IS NULL)", name="target_required"),
        Index("ib_job_requests_status_requested", "status", "requested_at"),
        {"schema": SCHEMA},
    )
