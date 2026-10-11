"""Letture e scritture del Backtesting Engine: lo storico di un portfolio
(cash e posizioni dagli snapshot, tutto lo storico, non un singolo run), il
benchmark dell'universo, e i risultati in `decisions.t_backtest_results`
(mai upsert: ogni backtest è una riga)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from marketmind_db.database import Transaction
from marketmind_db.models.decisions import BacktestResult
from marketmind_db.models.market_data import Asset, UniverseMember
from marketmind_db.models.portfolio import Portfolio, PortfolioPositionSnapshot, PortfolioSnapshot


@dataclass(frozen=True)
class PositionEvent:
    """Lo stato di una posizione dopo un trade (`DELETE`: la riga cancellata,
    quantità effettiva zero)."""

    asset_id: int
    symbol: str
    changed_at: datetime
    quantity: float
    avg_price: float
    operation: str


@dataclass(frozen=True)
class CashPoint:
    changed_at: datetime
    cash: float


@dataclass(frozen=True)
class PortfolioInfo:
    portfolio_id: int
    name: str
    starting_capital: float
    cash: float
    created_at: datetime


class BacktestRepository:
    def __init__(self, tx: Transaction) -> None:
        self.tx = tx

    def portfolio_info(self, portfolio_id: int) -> PortfolioInfo:
        p = self.tx.repository(Portfolio).get_one(portfolio_id=portfolio_id)
        if p is None:
            raise LookupError(f"portfolio {portfolio_id} inesistente")
        return PortfolioInfo(p.portfolio_id, p.name, p.starting_capital, p.cash, p.created_at)

    def position_events(self, portfolio_id: int) -> list[PositionEvent]:
        """Tutti gli eventi di posizione del portfolio, in ordine cronologico
        (a parità di istante, d'inserimento). Lista vuota = nessun trade."""
        rows = self.tx.repository(PortfolioPositionSnapshot).select(
            where={"portfolio_id": portfolio_id}, order_by=("changed_at", "snapshot_id"), limit=None
        )
        if not rows:
            return []
        assets = self.tx.repository(Asset).select(
            where={"asset_id": sorted({r.asset_id for r in rows})}, limit=None
        )
        symbols = {a.asset_id: a.symbol for a in assets}
        return [
            PositionEvent(r.asset_id, symbols[r.asset_id], r.changed_at, r.quantity, r.avg_price, r.operation)
            for r in rows
        ]

    def cash_history(self, portfolio_id: int) -> list[CashPoint]:
        rows = self.tx.repository(PortfolioSnapshot).select(
            where={"portfolio_id": portfolio_id}, order_by=("changed_at", "snapshot_id"), limit=None
        )
        return [CashPoint(r.changed_at, r.cash) for r in rows]

    def benchmark_asset_id(self) -> int | None:
        ids = self.tx.repository(UniverseMember).values(
            "asset_id", where={"is_benchmark": True}, order_by=("asset_id",), limit=1
        )
        return ids[0] if ids else None

    def write_result(
        self,
        *,
        portfolio_id: int,
        pnl: float,
        total_return: float | None,
        sharpe_ratio: float | None,
        max_drawdown: float | None,
        win_rate: float | None,
        benchmark_return: float | None,
        period_start: date,
        period_end: date,
    ) -> int:
        [row] = self.tx.repository(BacktestResult).insert_returning(
            [
                {
                    "portfolio_id": portfolio_id,
                    "pnl": pnl,
                    "total_return": total_return,
                    "sharpe_ratio": sharpe_ratio,
                    "max_drawdown": max_drawdown,
                    "win_rate": win_rate,
                    "benchmark_return": benchmark_return,
                    "period_start": period_start,
                    "period_end": period_end,
                }
            ],
            returning=("backtest_id",),
        )
        return row["backtest_id"]
