"""Letture e scritture del Backtesting Engine: gli eventi di posizione di un
run (dagli snapshot collegati via `marketmind.model_run_id`) e i risultati
in `decisions.t_backtest_results` (mai upsert: ogni backtest è una riga)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from marketmind_db.database import Transaction
from marketmind_db.models.decisions import BacktestResult
from marketmind_db.models.market_data import Asset
from marketmind_db.models.portfolio import PortfolioPositionSnapshot


@dataclass(frozen=True)
class PositionEvent:
    asset_id: int
    symbol: str
    changed_at: datetime
    quantity: float
    avg_price: float
    operation: str


class BacktestRepository:
    def __init__(self, tx: Transaction) -> None:
        self.tx = tx

    def position_events(self, portfolio_id: int, run_id: int) -> list[PositionEvent]:
        """Eventi di posizione originati da questo run, in ordine cronologico.
        Lista vuota = il run non ha eseguito trade."""
        rows = self.tx.repository(PortfolioPositionSnapshot).select(
            where={"portfolio_id": portfolio_id, "run_id": run_id}, order_by=("changed_at",), limit=None
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

    def write_result(
        self,
        *,
        run_id: int,
        pnl: float,
        sharpe_ratio: float | None,
        max_drawdown: float | None,
        win_rate: float | None,
        period_start: date,
        period_end: date,
    ) -> int:
        [row] = self.tx.repository(BacktestResult).insert_returning(
            [
                {
                    "run_id": run_id,
                    "pnl": pnl,
                    "sharpe_ratio": sharpe_ratio,
                    "max_drawdown": max_drawdown,
                    "win_rate": win_rate,
                    "period_start": period_start,
                    "period_end": period_end,
                }
            ],
            returning=("backtest_id",),
        )
        return row["backtest_id"]
