"""Backtesting Engine: analisi retrospettiva dei trade già eseguiti da un
run del motore decisionale (proof of concept).

Non esegue decisioni — quelle le esegue il Decision Engine al momento della
decisione. Ricostruisce gli ordini di un run dagli snapshot di posizione
collegati a quel run (`marketmind.model_run_id`) e li rigioca su vectorbt
(`Portfolio.from_orders`, `cash_sharing=True`, lo stesso disegno multi-asset
di produzione) per calcolare PnL, Sharpe, max drawdown e win rate.

`compute_metrics` è la parte vectorbt, pura; `Backtester` legge i dati da un
`BacktestStore` (`PostgresBacktestStore` in produzione) e salva il
risultato in `decisions.t_backtest_results`. Limite noto del POC: solo
eventi `INSERT` (un primo BUY su posizione vuota, dove `avg_price` è il
prezzo di esecuzione esatto); `UPDATE`/`DELETE` sollevano
`NotImplementedError` invece di produrre numeri sbagliati.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol

import pandas as pd
import vectorbt as vbt

from marketmind_db.database import Database
from marketmind_llm_decision_engine.repositories.backtest import BacktestRepository, PositionEvent
from marketmind_llm_decision_engine.repositories.market_context import MarketContextRepository
from marketmind_llm_decision_engine.repositories.portfolio import PortfolioRepository

# Il backtest è retrospettivo: "tutto lo storico disponibile" (nessuna fonte
# fa backfill profondo), non una finestra di contesto come per le decisioni.
FULL_HISTORY = timedelta(days=3650)

PricePoints = Sequence[tuple[datetime, float]]


class NoTradesForRunError(ValueError):
    """Il run non ha eseguito nessun BUY/SELL: niente da backtestare."""


@dataclass
class BacktestMetrics:
    pnl: float
    sharpe_ratio: float | None
    max_drawdown: float | None
    win_rate: float | None
    period_start: date
    period_end: date


def _none_if_nan(value: float) -> float | None:
    return None if pd.isna(value) else float(value)


def compute_metrics(
    events: Sequence[PositionEvent],
    prices_by_symbol: Mapping[str, PricePoints],
    *,
    starting_capital: float,
) -> BacktestMetrics:
    if not events:
        raise NoTradesForRunError("nessun trade da backtestare")
    for event in events:
        if event.operation != "INSERT":
            raise NotImplementedError(
                f"operation={event.operation!r} ({event.symbol}) non ancora supportata dal POC — "
                "vedi Market Mind AI - Docs/Backtest/00_motore_backtest.md"
            )

    columns: dict[str, pd.Series] = {}
    for event in events:
        series = pd.Series(dict(prices_by_symbol.get(event.symbol, [])), dtype=float)
        # Il prezzo di esecuzione entra nella serie anche se non coincide con
        # una barra nota: per un primo BUY avg_price è il prezzo esatto.
        series.loc[event.changed_at] = event.avg_price
        columns[event.symbol] = series.sort_index()
    close = pd.DataFrame(columns).sort_index().ffill().bfill()

    size = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    for event in events:
        size.loc[event.changed_at, event.symbol] = event.quantity

    pf = vbt.Portfolio.from_orders(
        close=close,
        size=size,
        size_type="amount",
        init_cash=starting_capital,
        cash_sharing=True,
        group_by=True,
        freq="1h",
    )
    return BacktestMetrics(
        pnl=float(pf.total_profit()),
        sharpe_ratio=_none_if_nan(pf.sharpe_ratio()),
        max_drawdown=_none_if_nan(pf.max_drawdown()),
        win_rate=_none_if_nan(pf.trades.win_rate()),
        period_start=min(e.changed_at for e in events).date(),
        period_end=close.index.max().date(),
    )


class BacktestStore(Protocol):
    def position_events(self, portfolio_id: int, run_id: int) -> list[PositionEvent]: ...

    def price_history(self, asset_id: int, *, as_of: datetime) -> list[tuple[datetime, float]]: ...

    def starting_capital_of(self, portfolio_id: int) -> float: ...

    def write_result(self, run_id: int, metrics: BacktestMetrics) -> int: ...


class PostgresBacktestStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def position_events(self, portfolio_id: int, run_id: int) -> list[PositionEvent]:
        with self.db.transaction() as tx:
            return BacktestRepository(tx).position_events(portfolio_id, run_id)

    def price_history(self, asset_id: int, *, as_of: datetime) -> list[tuple[datetime, float]]:
        with self.db.transaction() as tx:
            prices = MarketContextRepository(tx).prices(asset_id, as_of=as_of, days_back=FULL_HISTORY.days)
            return [(p.ts, p.close) for p in prices]

    def starting_capital_of(self, portfolio_id: int) -> float:
        with self.db.transaction() as tx:
            return PortfolioRepository(tx).get(portfolio_id).starting_capital

    def write_result(self, run_id: int, metrics: BacktestMetrics) -> int:
        with self.db.transaction() as tx:
            return BacktestRepository(tx).write_result(
                run_id=run_id,
                pnl=metrics.pnl,
                sharpe_ratio=metrics.sharpe_ratio,
                max_drawdown=metrics.max_drawdown,
                win_rate=metrics.win_rate,
                period_start=metrics.period_start,
                period_end=metrics.period_end,
            )


class Backtester:
    def __init__(self, store: BacktestStore) -> None:
        self.store = store

    def run(
        self, *, portfolio_id: int, run_id: int, as_of: datetime, save: bool = True
    ) -> tuple[BacktestMetrics, int | None]:
        """Backtest dei trade di `run_id`; con `save` scrive anche una riga
        nuova in `t_backtest_results` (mai upsert) e ne restituisce l'id."""
        events = self.store.position_events(portfolio_id, run_id)
        if not events:
            raise NoTradesForRunError(f"nessun trade collegato al run {run_id} (portfolio {portfolio_id})")
        prices = {
            e.symbol: self.store.price_history(e.asset_id, as_of=as_of)
            for e in {e.asset_id: e for e in events}.values()
        }
        metrics = compute_metrics(events, prices, starting_capital=self.store.starting_capital_of(portfolio_id))
        backtest_id = self.store.write_result(run_id, metrics) if save else None
        return metrics, backtest_id
