"""Valori letti dal DB per la dashboard.

Il repository restituisce questi oggetti, non istanze ORM: la sessione si
chiude subito dopo la lettura, e i calcoli (`analytics`) e la resa (`app`)
lavorano su valori immutabili, testabili senza DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class PortfolioView:
    portfolio_id: int
    name: str
    portfolio_type: str
    is_active: bool
    cash: float
    starting_capital: float
    created_at: datetime
    llm_provider: str | None = None
    model_version: str | None = None
    strategy_prompt: str | None = None
    next_decision_at: datetime | None = None

    @property
    def is_model(self) -> bool:
        return self.portfolio_type == "model"

    @property
    def label(self) -> str:
        status = "" if self.is_active else ", sospeso"
        return f"{self.name} ({self.portfolio_type}{status})"


@dataclass(frozen=True)
class PositionView:
    asset_id: int
    symbol: str
    quantity: float
    avg_price: float

    @property
    def cost(self) -> float:
        return self.quantity * self.avg_price


@dataclass(frozen=True)
class CashPoint:
    changed_at: datetime
    cash: float


@dataclass(frozen=True)
class PositionChange:
    """Una riga di `t_portfolio_position_snapshots`: lo stato della posizione
    dopo il cambiamento. Su `DELETE` il trigger registra la riga cancellata,
    quindi la quantità effettiva da quel momento è zero."""

    asset_id: int
    symbol: str
    changed_at: datetime
    quantity: float
    avg_price: float
    operation: str

    @property
    def quantity_after(self) -> float:
        return 0.0 if self.operation == "DELETE" else self.quantity


@dataclass(frozen=True)
class PricePoint:
    asset_id: int
    ts: datetime
    close: float


@dataclass(frozen=True)
class DecisionView:
    run_id: int
    ts: datetime
    symbol: str
    decision: str
    confidence: float | None
    size_pct: float | None
    reasoning: str | None
    # Rinvio chiesto dall'LLM e accettato dal motore (migrazione `0019`).
    recheck_at: datetime | None = None
    recheck_reason: str | None = None


@dataclass(frozen=True)
class RunView:
    """Un ciclo del Decision Engine (`t_model_runs`). `status` è NULL per i
    run precedenti alla migrazione `0017`, il cui esito non è ricostruibile."""

    run_id: int
    ts: datetime
    model_version: str
    status: str | None
    finished_at: datetime | None
    error_message: str | None
    decisions: int


@dataclass(frozen=True)
class BacktestView:
    backtest_id: int
    run_id: int
    pnl: float
    sharpe_ratio: float | None
    max_drawdown: float | None
    win_rate: float | None
    period_start: date
    period_end: date


@dataclass(frozen=True)
class IngestionRunView:
    source: str
    target_table: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    rows_written: int | None
    error_message: str | None
