"""Backtesting Engine: analisi retrospettiva dell'intero storico di un
portfolio su un periodo.

Non esegue decisioni — quelle le esegue il Decision Engine al momento della
decisione. Ricostruisce gli ordini davvero eseguiti dal portfolio dai suoi
snapshot (`reconstruct_orders`), li rigioca su vectorbt
(`Portfolio.from_orders`, `cash_sharing=True`, lo stesso disegno multi-asset
di produzione) dalla nascita del portfolio, e calcola le metriche sul
periodo richiesto (`compute_metrics`): PnL e rendimento, Sharpe, massimo
drawdown, win rate dei trade chiusi, rendimento del benchmark (SPY comprato
e tenuto) nello stesso periodo.

Il prezzo di esecuzione di un ordine non sta nello snapshot di posizione (su
un SELL il prezzo medio non cambia): è la variazione di cash dello stesso
trade divisa per la variazione di quantità, esatta sia per i BUY sia per i
SELL. Ogni trade cambia insieme il cash e una sola posizione, nella stessa
transazione, e nient'altro li cambia: l'i-esima variazione di cash
corrisponde all'i-esimo evento di posizione. Se i conteggi non tornano il
backtest si ferma (`InconsistentHistoryError`) invece di inventare prezzi.

La simulazione parte sempre dalla nascita del portfolio, perché vectorbt non
sa partire con posizioni già aperte; il periodo taglia solo le serie su cui
si calcolano le metriche. Il cash finale simulato si confronta con quello
reale del portfolio come controllo di coerenza.

`reconstruct_orders` e `compute_metrics` sono pure; `Backtester` legge i dati
da un `BacktestStore` (`PostgresBacktestStore` in produzione) e salva il
risultato in `decisions.t_backtest_results`.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol

import numpy as np
import pandas as pd
import vectorbt as vbt

from marketmind_db.database import Database
from marketmind_llm_decision_engine.repositories.backtest import (
    BacktestRepository,
    CashPoint,
    PortfolioInfo,
    PositionEvent,
)
from marketmind_llm_decision_engine.repositories.market_context import MarketContextRepository

logger = logging.getLogger(__name__)

# Il backtest è retrospettivo: "tutto lo storico disponibile" (nessuna fonte
# fa backfill profondo), non una finestra di contesto come per le decisioni.
FULL_HISTORY = timedelta(days=3650)
# Sharpe annualizzato sul valore giornaliero del portfolio, giorni di
# calendario (il valore resta fermo nel weekend, come i prezzi).
DAYS_PER_YEAR = 365
# Tolleranza sul confronto tra cash simulato e cash reale (arrotondamenti).
CASH_TOLERANCE = 0.01

PricePoints = Sequence[tuple[datetime, float]]


class NoTradesError(ValueError):
    """Il portfolio non ha eseguito nessun BUY/SELL: niente da backtestare."""


class InconsistentHistoryError(ValueError):
    """Variazioni di cash ed eventi di posizione non si corrispondono uno a uno."""


@dataclass(frozen=True)
class Order:
    """Un ordine eseguito: `size` positivo per un acquisto, negativo per una
    vendita, al prezzo di esecuzione reale."""

    ts: datetime
    asset_id: int
    symbol: str
    size: float
    price: float


@dataclass(frozen=True)
class BacktestPeriod:
    """Estremi inclusi del periodo su cui si calcolano le metriche."""

    start: datetime
    end: datetime


@dataclass
class BacktestMetrics:
    pnl: float
    total_return: float | None
    sharpe_ratio: float | None
    max_drawdown: float | None
    win_rate: float | None
    benchmark_return: float | None
    trades: int
    final_cash: float
    period_start: date
    period_end: date


def _none_if_nan(value: float) -> float | None:
    return None if value is None or pd.isna(value) else float(value)


def reconstruct_orders(events: Sequence[PositionEvent], cash_history: Sequence[CashPoint]) -> list[Order]:
    """Gli ordini eseguiti, in ordine, da eventi di posizione e storico del
    cash del portfolio (entrambi in ordine cronologico, a parità di istante
    d'inserimento)."""
    cash_changes = [
        (cash_history[i - 1].cash, cash_history[i].cash)
        for i in range(1, len(cash_history))
        if not math.isclose(cash_history[i].cash, cash_history[i - 1].cash, rel_tol=0, abs_tol=1e-9)
    ]
    if len(cash_changes) != len(events):
        raise InconsistentHistoryError(
            f"{len(events)} eventi di posizione, {len(cash_changes)} variazioni di cash: "
            "ogni trade dovrebbe cambiare entrambi"
        )
    held: dict[int, float] = {}
    orders = []
    for event, (cash_before, cash_after) in zip(events, cash_changes):
        after = 0.0 if event.operation == "DELETE" else event.quantity
        size = after - held.get(event.asset_id, 0.0)
        held[event.asset_id] = after
        if math.isclose(size, 0.0, abs_tol=1e-12):
            raise InconsistentHistoryError(f"evento {event.operation} su {event.symbol} senza variazione di quantità")
        orders.append(Order(event.changed_at, event.asset_id, event.symbol, size, abs(cash_after - cash_before) / abs(size)))
    return orders


def _strictly_increasing(orders: Sequence[Order]) -> list[Order]:
    """Ordini nello stesso istante (possibile solo se scritti nella stessa
    transazione, es. nei test) occuperebbero la stessa cella della matrice
    degli ordini e il secondo cancellerebbe il primo: si separano di un
    microsecondo, nell'ordine di esecuzione."""
    result: list[Order] = []
    for o in orders:
        if result and o.ts <= result[-1].ts:
            o = Order(result[-1].ts + timedelta(microseconds=1), o.asset_id, o.symbol, o.size, o.price)
        result.append(o)
    return result


def _value_at(series: pd.Series, ts: datetime, default: float) -> float:
    before = series.loc[:ts]
    return float(before.iloc[-1]) if not before.empty else default


def compute_metrics(
    orders: Sequence[Order],
    closes_by_symbol: Mapping[str, PricePoints],
    *,
    starting_capital: float,
    inception: datetime,
    period: BacktestPeriod,
    benchmark_closes: PricePoints,
) -> BacktestMetrics:
    if not orders:
        raise NoTradesError("nessun trade da backtestare")
    orders = _strictly_increasing(orders)

    symbols = list(dict.fromkeys(o.symbol for o in orders))
    columns = {}
    for symbol in symbols:
        series = pd.Series(dict(closes_by_symbol.get(symbol, [])), dtype=float)
        # Il prezzo di esecuzione fa da close negli istanti senza una barra:
        # serve a valorizzare la posizione appena aperta.
        for o in orders:
            if o.symbol == symbol and o.ts not in series.index:
                series.loc[o.ts] = o.price
        columns[symbol] = series[series.index <= period.end].sort_index()
    close = pd.DataFrame(columns)
    index = close.index.union(pd.DatetimeIndex([inception])).sort_values()
    index = index[(index >= inception) & (index <= period.end)]
    # Prima del primo prezzo noto la quantità è zero: il valore del bfill
    # non entra nell'equity.
    close = close.reindex(close.index.union(index)).sort_index().ffill().bfill().reindex(index)

    size = pd.DataFrame(np.nan, index=index, columns=symbols)
    price = pd.DataFrame(np.inf, index=index, columns=symbols)
    for o in orders:
        if o.ts <= period.end:
            size.loc[o.ts, o.symbol] = o.size
            price.loc[o.ts, o.symbol] = o.price

    pf = vbt.Portfolio.from_orders(
        close=close, size=size, price=price, size_type="amount", direction="longonly",
        init_cash=starting_capital, cash_sharing=True, group_by=True,
    )
    value = pf.value()
    start, end = max(period.start, inception), period.end
    start_value = _value_at(value, start, starting_capital)
    window = pd.concat([pd.Series([start_value], index=[start]), value[(value.index > start) & (value.index <= end)]])
    window = window[~window.index.duplicated(keep="last")]
    end_value = float(window.iloc[-1])

    daily = window.resample("1D").last().ffill()
    returns = daily.pct_change().dropna()
    sharpe = None
    if len(returns) >= 2 and returns.std() > 0:
        sharpe = float(returns.mean() / returns.std() * math.sqrt(DAYS_PER_YEAR))

    trades = pf.trades.records_readable
    closed = trades[(trades["Status"] == "Closed") & (trades["Exit Timestamp"] >= start) & (trades["Exit Timestamp"] <= end)]
    win_rate = float((closed["PnL"] > 0).mean()) if len(closed) else None

    benchmark = pd.Series(dict(benchmark_closes), dtype=float).sort_index()
    benchmark_return = None
    if not benchmark.empty:
        base = _value_at(benchmark, start, float(benchmark.iloc[0]))
        last = _value_at(benchmark, end, base)
        benchmark_return = last / base - 1 if base else None

    return BacktestMetrics(
        pnl=end_value - start_value,
        total_return=end_value / start_value - 1 if start_value else None,
        sharpe_ratio=sharpe,
        max_drawdown=_none_if_nan(float((window / window.cummax() - 1).min())),
        win_rate=win_rate,
        benchmark_return=benchmark_return,
        trades=sum(1 for o in orders if start <= o.ts <= end),
        final_cash=float(pf.cash().iloc[-1]),
        period_start=start.date(),
        period_end=end.date(),
    )


class BacktestStore(Protocol):
    def portfolio_info(self, portfolio_id: int) -> PortfolioInfo: ...

    def position_events(self, portfolio_id: int) -> list[PositionEvent]: ...

    def cash_history(self, portfolio_id: int) -> list[CashPoint]: ...

    def price_history(self, asset_id: int, *, as_of: datetime) -> list[tuple[datetime, float]]: ...

    def benchmark_history(self, *, as_of: datetime) -> list[tuple[datetime, float]]: ...

    def write_result(self, portfolio_id: int, metrics: BacktestMetrics) -> int: ...


class PostgresBacktestStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def portfolio_info(self, portfolio_id: int) -> PortfolioInfo:
        with self.db.transaction() as tx:
            return BacktestRepository(tx).portfolio_info(portfolio_id)

    def position_events(self, portfolio_id: int) -> list[PositionEvent]:
        with self.db.transaction() as tx:
            return BacktestRepository(tx).position_events(portfolio_id)

    def cash_history(self, portfolio_id: int) -> list[CashPoint]:
        with self.db.transaction() as tx:
            return BacktestRepository(tx).cash_history(portfolio_id)

    def price_history(self, asset_id: int, *, as_of: datetime) -> list[tuple[datetime, float]]:
        with self.db.transaction() as tx:
            prices = MarketContextRepository(tx).prices(asset_id, as_of=as_of, days_back=FULL_HISTORY.days)
            return [(p.ts, p.close) for p in prices]

    def benchmark_history(self, *, as_of: datetime) -> list[tuple[datetime, float]]:
        with self.db.transaction() as tx:
            asset_id = BacktestRepository(tx).benchmark_asset_id()
            if asset_id is None:
                return []
            prices = MarketContextRepository(tx).prices(asset_id, as_of=as_of, days_back=FULL_HISTORY.days)
            return [(p.ts, p.close) for p in prices]

    def write_result(self, portfolio_id: int, metrics: BacktestMetrics) -> int:
        with self.db.transaction() as tx:
            return BacktestRepository(tx).write_result(
                portfolio_id=portfolio_id,
                pnl=metrics.pnl,
                total_return=metrics.total_return,
                sharpe_ratio=metrics.sharpe_ratio,
                max_drawdown=metrics.max_drawdown,
                win_rate=metrics.win_rate,
                benchmark_return=metrics.benchmark_return,
                period_start=metrics.period_start,
                period_end=metrics.period_end,
            )


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


class Backtester:
    def __init__(self, store: BacktestStore) -> None:
        self.store = store

    def run(
        self,
        *,
        portfolio_id: int,
        as_of: datetime,
        start: date | None = None,
        end: date | None = None,
        save: bool = True,
    ) -> tuple[BacktestMetrics, int | None]:
        """Backtest del portfolio tra `start` e `end` inclusi (default: dalla
        nascita del portfolio ad `as_of`); con `save` scrive anche una riga
        nuova in `t_backtest_results` (mai upsert) e ne restituisce l'id."""
        if start is not None and end is not None and start > end:
            raise ValueError(f"periodo non valido: {start} è dopo {end}")
        info = self.store.portfolio_info(portfolio_id)
        orders = reconstruct_orders(self.store.position_events(portfolio_id), self.store.cash_history(portfolio_id))
        if not orders:
            raise NoTradesError(f"il portfolio {info.name} non ha ancora eseguito trade")

        period = BacktestPeriod(
            start=info.created_at if start is None else max(_day_start(start), info.created_at),
            end=as_of if end is None else min(as_of, _day_start(end + timedelta(days=1)) - timedelta(microseconds=1)),
        )
        closes = {
            o.symbol: self.store.price_history(o.asset_id, as_of=as_of)
            for o in {o.asset_id: o for o in orders}.values()
        }
        metrics = compute_metrics(
            orders, closes, starting_capital=info.starting_capital, inception=info.created_at, period=period,
            benchmark_closes=self.store.benchmark_history(as_of=as_of),
        )
        if all(o.ts <= period.end for o in orders) and not math.isclose(
            metrics.final_cash, info.cash, rel_tol=0, abs_tol=CASH_TOLERANCE
        ):
            logger.warning(
                "portfolio %s: cash simulato %.2f diverso da quello reale %.2f — storico incompleto?",
                info.name, metrics.final_cash, info.cash,
            )
        backtest_id = self.store.write_result(portfolio_id, metrics) if save else None
        return metrics, backtest_id
