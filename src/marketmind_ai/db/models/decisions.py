"""Tabelle dello schema `decisions`.

`t_model_runs`/`t_model_decisions` usano una singola colonna `ts` (non più
`run_ts`/`decision_ts`) — normalizzazione decisa il 29-08-26.

NOTA: come per `market_data`, i dettagli di colonna oltre alle decisioni
esplicite in CLAUDE.md sono una prima bozza in assenza del documento ER.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from marketmind_ai.db.base import Base

SCHEMA = "decisions"


class ModelRun(Base):
    """Una esecuzione del motore decisionale (batch settimanale su tutti gli asset)."""

    __tablename__ = "t_model_runs"
    __table_args__ = {"schema": SCHEMA}

    run_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    llm_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    llm_model: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="pending")
    run_metadata: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()"
    )

    decisions_made: Mapped[list["ModelDecision"]] = relationship(back_populates="run")
    backtest_results: Mapped[list["BacktestResult"]] = relationship(back_populates="run")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="status",
        ),
        {"schema": SCHEMA},
    )


class ModelDecision(Base):
    """La decisione BUY/SELL/HOLD presa dall'LLM per un asset in un run."""

    __tablename__ = "t_model_decisions"

    decision_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.t_model_runs.run_id"), nullable=False
    )
    asset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("market_data.t_assets.asset_id"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    rationale: Mapped[str | None] = mapped_column(Text)
    context_window: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()"
    )

    run: Mapped["ModelRun"] = relationship(back_populates="decisions_made")

    __table_args__ = (
        UniqueConstraint("run_id", "asset_id", name="uq_t_model_decisions_run_id_asset_id"),
        CheckConstraint("decision IN ('BUY', 'SELL', 'HOLD')", name="decision"),
        {"schema": SCHEMA},
    )


class BacktestResult(Base):
    """Esito del backtest (vectorbt, `cash_sharing=True`) su un periodo.

    Le metriche (return, sharpe, drawdown, ...) restano in `metrics` come
    JSONB invece di essere elencate come colonne rigide: l'insieme esatto
    delle statistiche da salvare non è ancora stato deciso.
    """

    __tablename__ = "t_backtest_results"

    backtest_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.t_model_runs.run_id"), nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()"
    )

    run: Mapped["ModelRun"] = relationship(back_populates="backtest_results")

    __table_args__ = {"schema": SCHEMA}
