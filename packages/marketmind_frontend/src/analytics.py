"""Calcoli della dashboard: equity curve mark-to-market, valorizzazione delle
posizioni, confronto col benchmark.

Il valore di mercato di un portfolio non è salvato nel DB (migrazione
`0018`): si calcola qui, in sola lettura. L'equity in ogni istante è `cash` + Σ quantità × ultimo close noto a
quell'istante, ricostruita da `t_portfolio_snapshots` (cash),
`t_portfolio_position_snapshots` (quantità) e `t_market_prices` (close).
Un asset senza alcun close noto è valorizzato al prezzo medio di carico,
cioè al costo: meglio di zero, che farebbe crollare la curva al primo BUY.

Funzioni pure su valori (`views`), nessun DB e nessuno Streamlit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from marketmind_frontend.views import CashPoint, PositionChange, PositionView, PricePoint

_CURVE_COLUMNS = ["cash", "positions", "equity"]


def _wide(points: Sequence[tuple[datetime, int, float]]) -> pd.DataFrame:
    """Serie per asset (una colonna per `asset_id`) indicizzate per tempo;
    a parità di istante vince l'ultimo valore."""
    if not points:
        return pd.DataFrame()
    frame = pd.DataFrame(points, columns=["ts", "asset_id", "value"])
    return frame.pivot_table(index="ts", columns="asset_id", values="value", aggfunc="last").sort_index()


def equity_curve(
    cash_history: Sequence[CashPoint],
    changes: Sequence[PositionChange],
    prices: Sequence[PricePoint],
) -> pd.DataFrame:
    """Colonne `cash`, `positions` (valore di mercato) ed `equity`, indicizzate
    per tempo: un punto a ogni cambiamento di cash o di posizione e a ogni
    barra di prezzo di un asset, dal primo snapshot di cash in poi. Le barre
    precedenti servono solo a dare un prezzo ai primi punti."""
    if not cash_history:
        return pd.DataFrame(columns=_CURVE_COLUMNS, dtype=float)

    cash = pd.Series(
        [p.cash for p in cash_history], index=[p.changed_at for p in cash_history], dtype=float
    )
    cash = cash[~cash.index.duplicated(keep="last")].sort_index()
    start = cash.index[0]

    quantities = _wide([(c.changed_at, c.asset_id, c.quantity_after) for c in changes])
    avg_prices = _wide([(c.changed_at, c.asset_id, c.avg_price) for c in changes])
    closes = _wide([(p.ts, p.asset_id, p.close) for p in prices])

    timeline = cash.index.union(quantities.index).union(closes.index)
    timeline = timeline[timeline >= start]

    if quantities.empty:
        positions = pd.Series(0.0, index=timeline)
    else:
        qty = quantities.reindex(quantities.index.union(timeline)).ffill().fillna(0.0).reindex(timeline)
        fallback = avg_prices.reindex(avg_prices.index.union(timeline)).ffill().reindex(timeline)
        if closes.empty:
            price = fallback
        else:
            price = closes.reindex(closes.index.union(timeline)).ffill().reindex(timeline)
            price = price.reindex(columns=qty.columns).combine_first(fallback)
        positions = (qty * price.reindex(columns=qty.columns)).fillna(0.0).sum(axis=1)

    curve = pd.DataFrame(
        {"cash": cash.reindex(cash.index.union(timeline)).ffill().reindex(timeline), "positions": positions}
    )
    curve["equity"] = curve["cash"] + curve["positions"]
    return curve


@dataclass(frozen=True)
class PositionValuation:
    symbol: str
    quantity: float
    avg_price: float
    cost: float
    last_price: float | None
    price_ts: datetime | None
    market_value: float
    pnl: float
    pnl_pct: float | None


def valuation(
    positions: Sequence[PositionView], latest: Mapping[int, PricePoint]
) -> list[PositionValuation]:
    """Valore di mercato e P&L non realizzato di ogni posizione all'ultimo
    close noto (`latest`, per `asset_id`); senza un prezzo, valore = costo."""
    rows = []
    for p in positions:
        price = latest.get(p.asset_id)
        value = p.quantity * price.close if price else p.cost
        pnl = value - p.cost
        rows.append(
            PositionValuation(
                symbol=p.symbol,
                quantity=p.quantity,
                avg_price=p.avg_price,
                cost=p.cost,
                last_price=price.close if price else None,
                price_ts=price.ts if price else None,
                market_value=value,
                pnl=pnl,
                pnl_pct=pnl / p.cost if p.cost else None,
            )
        )
    return rows


@dataclass(frozen=True)
class PerformanceSummary:
    equity: float | None
    total_return: float | None
    max_drawdown: float | None


def performance_summary(curve: pd.DataFrame, *, starting_capital: float) -> PerformanceSummary:
    """Equity all'ultimo punto, rendimento sul capitale iniziale e massimo
    drawdown (negativo) della curva. Nessuno Sharpe: con punti a intervalli
    irregolari (trade e barre orarie) non sarebbe un numero confrontabile."""
    if curve.empty:
        return PerformanceSummary(None, None, None)
    equity = curve["equity"]
    drawdown = equity / equity.cummax() - 1
    last = float(equity.iloc[-1])
    return PerformanceSummary(
        equity=last,
        total_return=last / starting_capital - 1 if starting_capital else None,
        max_drawdown=float(drawdown.min()),
    )


def benchmark_comparison(
    curve: pd.DataFrame, starting_capital: float, benchmark: Sequence[PricePoint]
) -> pd.DataFrame:
    """Rendimento cumulato del portfolio (`portfolio`, sul capitale iniziale)
    e del benchmark comprato e tenuto dall'inizio della curva (`benchmark`,
    sull'ultimo close noto all'inizio, o sulla sua prima barra se non ce
    n'è uno precedente). Colonna `benchmark` vuota senza prezzi."""
    if curve.empty:
        return pd.DataFrame(columns=["portfolio", "benchmark"], dtype=float)
    start = curve.index[0]
    closes = pd.Series([p.close for p in benchmark], index=[p.ts for p in benchmark], dtype=float)
    closes = closes[~closes.index.duplicated(keep="last")].sort_index()

    timeline = curve.index.union(closes.index[closes.index >= start])
    equity = curve["equity"].reindex(timeline).ffill()
    result = pd.DataFrame({"portfolio": equity / starting_capital - 1}, index=timeline)

    if closes.empty:
        result["benchmark"] = float("nan")
        return result
    before = closes[closes.index <= start]
    base = before.iloc[-1] if not before.empty else closes.iloc[0]
    result["benchmark"] = closes.reindex(closes.index.union(timeline)).ffill().reindex(timeline) / base - 1
    return result
