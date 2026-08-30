"""Tabelle dello schema `decisions`.

Rispecchia colonna per colonna l'`erDiagram` confermato in
`Market Mind AI - Docs/Architettura/01_schema_dati_er.md`. `t_model_runs`/
`t_model_decisions` usano una singola colonna `ts` (non più `run_ts`/
`decision_ts`) — normalizzazione decisa il 29-08-26 (agenda #19). Le tre
tabelle dipendono solo da `t_assets` e tra loro, mai direttamente dalle
tabelle di ingestion.

Il tracking del portafoglio virtuale (`t_portfolios` e le tabelle
collegate) NON vive qui: è stato introdotto qui il 29-08-26 ma spostato in
un proprio schema dedicato, `portfolio` (`db/models/portfolio.py`), il
30-08-26 — il portafoglio è lo stato che risulta dall'eseguire le
decisioni nel tempo, non una decisione in sé (agenda #35).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Double, ForeignKey, Index, String, Text
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
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    llm_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)

    decisions_made: Mapped[list["ModelDecision"]] = relationship(back_populates="run")
    backtest_results: Mapped[list["BacktestResult"]] = relationship(back_populates="run")


class ModelDecision(Base):
    """La decisione BUY/SELL/HOLD presa dall'LLM per un asset in un run.

    `context_snapshot` salva esattamente il contesto che l'Historical
    Context Builder ha passato al modello — utile per audit e per
    riprodurre una decisione a posteriori.
    """

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
    confidence: Mapped[float | None] = mapped_column(Double)
    reasoning: Mapped[str | None] = mapped_column(Text)
    context_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    run: Mapped["ModelRun"] = relationship(back_populates="decisions_made")

    __table_args__ = (
        CheckConstraint("decision IN ('BUY', 'SELL', 'HOLD')", name="decision"),
        Index("ib_model_decisions_asset_ts", "asset_id", "ts"),
        Index("ib_model_decisions_run_id", "run_id"),
        {"schema": SCHEMA},
    )


class BacktestResult(Base):
    """Esito del backtest (vectorbt, `cash_sharing=True`) su un periodo."""

    __tablename__ = "t_backtest_results"

    backtest_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.t_model_runs.run_id"), nullable=False
    )
    pnl: Mapped[float] = mapped_column(Double, nullable=False)
    sharpe_ratio: Mapped[float | None] = mapped_column(Double)
    max_drawdown: Mapped[float | None] = mapped_column(Double)
    win_rate: Mapped[float | None] = mapped_column(Double)
    period_start: Mapped[date] = mapped_column(nullable=False)
    period_end: Mapped[date] = mapped_column(nullable=False)

    run: Mapped["ModelRun"] = relationship(back_populates="backtest_results")

    __table_args__ = (
        Index("ib_backtest_results_run_id", "run_id"),
        {"schema": SCHEMA},
    )
