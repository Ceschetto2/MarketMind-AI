"""Tabelle dello schema `portfolio` — tracking del portafoglio virtuale.

Rispecchia colonna per colonna l'`erDiagram` confermato in
`Market Mind AI - Docs/Architettura/01_schema_dati_er.md`. Le quattro
tabelle sono state introdotte il 29-08-26 dentro `decisions`, poi spostate
in questo schema dedicato il 30-08-26 (agenda #35): il portafoglio è lo
stato applicativo che *risulta* dall'eseguire nel tempo le decisioni
dell'LLM, non una decisione in sé — un dominio a sé rispetto a
`t_model_runs`/`t_model_decisions`/`t_backtest_results`, con un proprio
ciclo di vita (stato corrente mutabile + storico) che non condivide nulla
strutturalmente con quelle tre tabelle. Nessun cambio a tabelle/colonne/
vincoli rispetto alla stesura del 29-08-26, solo allo schema Postgres che
le contiene.

Pattern: `t_portfolios`/`t_portfolio_positions` sono stato corrente
mutabile; `t_portfolio_snapshots`/`t_portfolio_position_snapshots` sono
storico append-only alimentato da due trigger Postgres dedicati e
tipizzati (`fn_portfolio_snapshot`/`fn_portfolio_position_snapshot`) — non
il meccanismo generico `audit.fn_audit_log()` di `t_audit_logs`, perché
un'equity curve richiede sommare `quantity`/`cash`/`equity_value`
direttamente in SQL, non estrarli da JSONB. Le due tabelle di storico sono
di sola lettura da ORM, come `AuditLog`.

L'unica FK che attraversa il confine con lo schema `decisions` è quella
nullable verso `t_model_runs.run_id` sulle due tabelle di storico — uno
snapshot può, ma non deve, essere collegato al run che lo ha originato;
supportata nativamente da Postgres (foreign key cross-schema).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from marketmind_ai.db.base import Base

SCHEMA = "portfolio"


class Portfolio(Base):
    """Contenitore generico per un portafoglio virtuale — modello o benchmark.

    Non un singleton: `portfolio_type` distingue il portafoglio guidato
    dalle decisioni dell'LLM (`'model'`) dal benchmark di confronto
    (`'benchmark'`, es. SPY buy & hold) — righe distinte dello stesso
    meccanismo, nessuna tabella o colonna speciale per il benchmark. `cash`
    ed `equity_value` sono mutabili, scritti dallo strato applicativo a ogni
    trade/mark-to-market; il trigger su questa tabella si limita a
    fotografarli in `t_portfolio_snapshots` ad ogni cambiamento.
    """

    __tablename__ = "t_portfolios"

    portfolio_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    portfolio_type: Mapped[str] = mapped_column(String(20), nullable=False)
    starting_capital: Mapped[float] = mapped_column(Double, nullable=False)
    cash: Mapped[float] = mapped_column(Double, nullable=False)
    equity_value: Mapped[float] = mapped_column(Double, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    positions: Mapped[list["PortfolioPosition"]] = relationship(
        back_populates="portfolio"
    )

    __table_args__ = (
        CheckConstraint(
            "portfolio_type IN ('model', 'benchmark')", name="portfolio_type"
        ),
        {"schema": SCHEMA},
    )


class PortfolioPosition(Base):
    """Stato corrente (mutabile) della posizione di un asset in un portafoglio.

    PK composita `(portfolio_id, asset_id)`: un asset compare al più una
    volta per portafoglio. `quantity` è attesa >= 0 — lo short selling non è
    contemplato nel design attuale, ma non è un vincolo `CHECK` esplicito, in
    attesa di una decisione dedicata se lo scope dovesse mai includerlo.
    """

    __tablename__ = "t_portfolio_positions"

    portfolio_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.t_portfolios.portfolio_id"), primary_key=True
    )
    asset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("market_data.t_assets.asset_id"), primary_key=True
    )
    quantity: Mapped[float] = mapped_column(Double, nullable=False)
    avg_price: Mapped[float] = mapped_column(Double, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    portfolio: Mapped["Portfolio"] = relationship(back_populates="positions")

    __table_args__ = {"schema": SCHEMA}


class PortfolioSnapshot(Base):
    """Storico append-only di `t_portfolios`, alimentato dal trigger
    `portfolio.fn_portfolio_snapshot()` — sola lettura da ORM.
    """

    __tablename__ = "t_portfolio_snapshots"

    snapshot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.t_portfolios.portfolio_id"), nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("decisions.t_model_runs.run_id")
    )
    cash: Mapped[float] = mapped_column(Double, nullable=False)
    equity_value: Mapped[float] = mapped_column(Double, nullable=False)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "operation IN ('INSERT', 'UPDATE', 'DELETE')", name="operation"
        ),
        Index("ib_portfolio_snapshots_portfolio_changed", "portfolio_id", "changed_at"),
        Index("ib_portfolio_snapshots_run_id", "run_id"),
        {"schema": SCHEMA},
    )


class PortfolioPositionSnapshot(Base):
    """Storico append-only di `t_portfolio_positions`, alimentato dal trigger
    `portfolio.fn_portfolio_position_snapshot()` — sola lettura da ORM.
    """

    __tablename__ = "t_portfolio_position_snapshots"

    snapshot_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.t_portfolios.portfolio_id"), nullable=False
    )
    asset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("market_data.t_assets.asset_id"), nullable=False
    )
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("decisions.t_model_runs.run_id")
    )
    quantity: Mapped[float] = mapped_column(Double, nullable=False)
    avg_price: Mapped[float] = mapped_column(Double, nullable=False)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "operation IN ('INSERT', 'UPDATE', 'DELETE')", name="operation"
        ),
        Index(
            "ib_portfolio_position_snapshots_portfolio_asset_changed",
            "portfolio_id",
            "asset_id",
            "changed_at",
        ),
        Index("ib_portfolio_position_snapshots_run_id", "run_id"),
        {"schema": SCHEMA},
    )
