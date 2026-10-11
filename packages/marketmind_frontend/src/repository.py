"""Letture della dashboard su `portfolio`, `decisions`, `market_data` e `audit`.

Sopra `TableRepository` (policy `READ_ONLY`, ruolo `marketmind_readonly`),
come i repository del Decision Engine: nessun SQL scritto a mano, i join
diventano due query semplici (prima gli id, poi le righe per id). Il modulo
vive nel pacchetto `marketmind_frontend`, non in `marketmind_db` (solo lo
strato generico) né nel pacchetto del Decision Engine, da cui la dashboard
non dipende. Restituisce valori (`views`), non istanze ORM.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import datetime

from marketmind_db.database import Transaction
from marketmind_db.models.audit import IngestionRun
from marketmind_db.models.decisions import BacktestResult, ModelDecision, ModelRun
from marketmind_db.models.market_data import Asset, MarketPrice, UniverseMember
from marketmind_db.models.portfolio import (
    Portfolio,
    PortfolioPosition,
    PortfolioPositionSnapshot,
    PortfolioSnapshot,
    PortfolioWatchlistEntry,
)
from marketmind_frontend.views import (
    BacktestView,
    CashPoint,
    DecisionView,
    IngestionRunView,
    PortfolioView,
    PositionChange,
    PositionView,
    PricePoint,
    RunView,
)


def _portfolio_view(p: Portfolio) -> PortfolioView:
    return PortfolioView(
        portfolio_id=p.portfolio_id,
        name=p.name,
        portfolio_type=p.portfolio_type,
        is_active=p.is_active,
        cash=p.cash,
        starting_capital=p.starting_capital,
        created_at=p.created_at,
        llm_provider=p.llm_provider,
        model_version=p.model_version,
        strategy_prompt=p.strategy_prompt,
        next_decision_at=p.next_decision_at,
    )


class DashboardRepository:
    def __init__(self, tx: Transaction) -> None:
        self.tx = tx

    # --- portfolio -------------------------------------------------------------

    def portfolios(self) -> list[PortfolioView]:
        """Tutti i portfolio, `model` e `benchmark`, attivi o sospesi: a
        differenza del Decision Engine, la dashboard mostra anche quelli su
        cui il motore non gira."""
        rows = self.tx.repository(Portfolio).select(order_by=("name",), limit=None)
        return [_portfolio_view(p) for p in rows]

    def portfolio(self, portfolio_id: int) -> PortfolioView:
        row = self.tx.repository(Portfolio).get_one(portfolio_id=portfolio_id)
        if row is None:
            raise LookupError(f"portfolio {portfolio_id} inesistente")
        return _portfolio_view(row)

    def positions(self, portfolio_id: int) -> list[PositionView]:
        rows = self.tx.repository(PortfolioPosition).select(where={"portfolio_id": portfolio_id}, limit=None)
        symbols = self._symbols(r.asset_id for r in rows)
        views = [PositionView(r.asset_id, symbols[r.asset_id], r.quantity, r.avg_price) for r in rows]
        return sorted(views, key=lambda v: v.symbol)

    def watchlist(self, portfolio_id: int) -> list[str]:
        ids = self.tx.repository(PortfolioWatchlistEntry).values(
            "asset_id", where={"portfolio_id": portfolio_id}, limit=None
        )
        return sorted(self._symbols(ids).values())

    def cash_history(self, portfolio_id: int) -> list[CashPoint]:
        """L'intera serie di `cash` da `t_portfolio_snapshots`, in ordine
        cronologico (a parità di istante, d'inserimento)."""
        rows = self.tx.repository(PortfolioSnapshot).select(
            where={"portfolio_id": portfolio_id}, order_by=("changed_at", "snapshot_id"), limit=None
        )
        return [CashPoint(r.changed_at, r.cash) for r in rows]

    def position_changes(self, portfolio_id: int) -> list[PositionChange]:
        rows = self.tx.repository(PortfolioPositionSnapshot).select(
            where={"portfolio_id": portfolio_id}, order_by=("changed_at", "snapshot_id"), limit=None
        )
        symbols = self._symbols(r.asset_id for r in rows)
        return [
            PositionChange(r.asset_id, symbols[r.asset_id], r.changed_at, r.quantity, r.avg_price, r.operation)
            for r in rows
        ]

    # --- prezzi ----------------------------------------------------------------

    def prices(self, asset_ids: Sequence[int], *, since: datetime) -> list[PricePoint]:
        if not asset_ids:
            return []
        rows = self.tx.repository(MarketPrice).select(
            where=[MarketPrice.asset_id.in_(list(asset_ids)), MarketPrice.ts >= since],
            order_by=("ts",),
            limit=None,
        )
        return [PricePoint(r.asset_id, r.ts, r.close) for r in rows]

    def latest_prices(self, asset_ids: Sequence[int]) -> dict[int, PricePoint]:
        """L'ultimo close noto per ogni asset (`DISTINCT ON`)."""
        if not asset_ids:
            return {}
        rows = self.tx.repository(MarketPrice).latest_per(
            ("asset_id",), order_by="ts", where={"asset_id": list(asset_ids)}, limit=None
        )
        return {r.asset_id: PricePoint(r.asset_id, r.ts, r.close) for r in rows}

    def benchmark_asset(self) -> tuple[int, str] | None:
        """L'asset benchmark dell'universo (`is_benchmark`, oggi SPY)."""
        ids = self.tx.repository(UniverseMember).values(
            "asset_id", where={"is_benchmark": True}, order_by=("asset_id",), limit=1
        )
        if not ids:
            return None
        return ids[0], self._symbols(ids)[ids[0]]

    # --- decision engine -------------------------------------------------------

    def decisions(self, portfolio_id: int, *, limit: int = 200) -> list[DecisionView]:
        run_ids = self._run_ids(portfolio_id)
        if not run_ids:
            return []
        rows = self.tx.repository(ModelDecision).select(
            where={"run_id": run_ids}, order_by=("-ts", "-decision_id"), limit=limit
        )
        symbols = self._symbols(r.asset_id for r in rows)
        return [
            DecisionView(r.run_id, r.ts, symbols[r.asset_id], r.decision, r.confidence, r.size_pct, r.reasoning,
                         r.recheck_at, r.recheck_reason)
            for r in rows
        ]

    def runs(self, portfolio_id: int, *, limit: int = 50) -> list[RunView]:
        """I cicli più recenti con esito ed errore, e quante decisioni hanno."""
        rows = self.tx.repository(ModelRun).select(
            where={"portfolio_id": portfolio_id}, order_by=("-ts", "-run_id"), limit=limit
        )
        counts: Counter[int] = Counter()
        if rows:
            counts.update(
                self.tx.repository(ModelDecision).values(
                    "run_id", where={"run_id": [r.run_id for r in rows]}, limit=None
                )
            )
        return [
            RunView(r.run_id, r.ts, r.model_version, r.status, r.finished_at, r.error_message, counts[r.run_id])
            for r in rows
        ]

    def backtests(self, portfolio_id: int) -> list[BacktestView]:
        """I backtest del portfolio, il più recente prima."""
        rows = self.tx.repository(BacktestResult).select(
            where={"portfolio_id": portfolio_id}, order_by=("-backtest_id",), limit=None
        )
        return [
            BacktestView(r.backtest_id, r.run_id, r.pnl, r.sharpe_ratio, r.max_drawdown, r.win_rate,
                         r.period_start, r.period_end, r.total_return, r.benchmark_return, r.created_at)
            for r in rows
        ]

    # --- ingestion -------------------------------------------------------------

    def latest_ingestion_runs(self) -> list[IngestionRunView]:
        """L'ultimo run di ogni pipeline (stessi `source` e `target_table`)."""
        rows = self.tx.repository(IngestionRun).latest_per(
            ("source", "target_table"), order_by="started_at", limit=None
        )
        return [
            IngestionRunView(r.source, r.target_table, r.started_at, r.finished_at, r.status, r.rows_written,
                             r.error_message)
            for r in sorted(rows, key=lambda r: (r.source, r.target_table))
        ]

    # --- interni ---------------------------------------------------------------

    def _run_ids(self, portfolio_id: int) -> list[int]:
        return self.tx.repository(ModelRun).values("run_id", where={"portfolio_id": portfolio_id}, limit=None)

    def _symbols(self, asset_ids: Iterable[int]) -> dict[int, str]:
        ids = sorted(set(asset_ids))
        if not ids:
            return {}
        assets = self.tx.repository(Asset).select(where={"asset_id": ids}, limit=None)
        return {a.asset_id: a.symbol for a in assets}
