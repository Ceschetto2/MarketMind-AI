"""Backtesting Engine — analisi retrospettiva (vectorbt) su trade già
eseguiti da `decision_engine/`, non simulazione dell'esecuzione (già
avvenuta per davvero, `Market Mind AI - Docs/Backtest/00_motore_backtest.md`).

POC: valida l'integrazione vectorbt su un run reale con trade eseguiti
(oggi solo `gemma-growth-test`/run 41 ne ha, §Roadmap `Market Mind AI.md`)
prima di scalare all'intero universo — un portfolio multi-asset non è più
complesso da gestire di un singolo asset con `cash_sharing=True` fin da
questa prima versione, lo stesso disegno di produzione (§3 `Market Mind
AI.md`), quindi il POC parte direttamente da lì invece di un caso ancora
più ridotto.

Limite noto, non generalizzato in questa prima versione: ricostruisce
l'ordine eseguito da `t_portfolio_position_snapshots` assumendo un solo
BUY per asset su una posizione precedentemente vuota (`operation=
'INSERT'`), dove `avg_price` coincide esattamente col prezzo di esecuzione
usato da `execute_trade()` — vero per i trade di oggi. Un `UPDATE` (BUY
successivo sulla stessa posizione, `avg_price` diventato una media pesata,
non più il prezzo del singolo ordine) o un `DELETE` (SELL totale) fanno
sollevare `NotImplementedError` invece di produrre numeri silenziosamente
sbagliati — da estendere quando un run reale li produrrà.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd
import vectorbt as vbt
from sqlalchemy.orm import Session

from marketmind_ai.db.backtest_reader import get_position_events
from marketmind_ai.db.context_reader import get_recent_prices
from marketmind_ai.db.portfolio_reader import get_portfolio

# Nessuna fonte prevede un backfill storico profondo (CLAUDE.md): una
# finestra "enorme" qui significa in pratica "tutto lo storico disponibile",
# non un vincolo reale di windowing come quelli dell'Historical Context
# Builder — il Backtesting Engine è retrospettivo, il principio
# no-look-ahead non si applica alla sua stessa lettura dei prezzi.
_FULL_HISTORY_DAYS_BACK = 3650


class NoTradesForRunError(ValueError):
    """Nessun trade (BUY/SELL eseguito) collegato a questo run — un run di
    soli HOLD, o un run non ancora eseguito, non ha nulla da backtestare."""


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


def run_backtest(
    session: Session, portfolio_id: int, run_id: int, as_of: datetime
) -> BacktestMetrics:
    """Rigioca su vectorbt i trade eseguiti da `run_id` per `portfolio_id`
    (`vbt.Portfolio.from_orders`, `cash_sharing=True` — stesso disegno di
    produzione) e ne calcola pnl/Sharpe/max drawdown/win rate. Solleva
    `NoTradesForRunError` se il run non ha eseguito nessun BUY/SELL.
    """
    events = get_position_events(session, portfolio_id, run_id)
    if not events:
        raise NoTradesForRunError(
            f"nessun trade collegato al run {run_id} (portfolio {portfolio_id})"
        )
    for event in events:
        if event.operation != "INSERT":
            raise NotImplementedError(
                f"operation={event.operation!r} (asset {event.asset.symbol}) non "
                "ancora supportata dal POC — vedi il limite noto in "
                "Market Mind AI - Docs/Backtest/00_motore_backtest.md"
            )

    portfolio = get_portfolio(session, portfolio_id)

    price_series: dict[str, pd.Series] = {}
    for event in events:
        prices = get_recent_prices(
            session, event.asset_id, as_of, days_back=_FULL_HISTORY_DAYS_BACK
        )
        series = pd.Series({p.ts: p.close for p in prices}, dtype=float)
        # Il bar dell'esecuzione entra comunque nella serie anche se non
        # coincide con una barra nota: per un primo BUY su posizione vuota
        # avg_price *è* il prezzo di esecuzione esatto usato da
        # execute_trade() (context.prices[-1].close), nessuna media da fare.
        series.loc[event.changed_at] = event.avg_price
        price_series[event.asset.symbol] = series.sort_index()

    close = pd.DataFrame(price_series).sort_index().ffill().bfill()

    size = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
    for event in events:
        size.loc[event.changed_at, event.asset.symbol] = event.quantity

    pf = vbt.Portfolio.from_orders(
        close=close,
        size=size,
        size_type="amount",
        init_cash=portfolio.starting_capital,
        cash_sharing=True,
        group_by=True,
        freq="1h",
    )

    return BacktestMetrics(
        pnl=float(pf.total_profit()),
        sharpe_ratio=_none_if_nan(pf.sharpe_ratio()),
        max_drawdown=_none_if_nan(pf.max_drawdown()),
        win_rate=_none_if_nan(pf.trades.win_rate()),
        period_start=min(event.changed_at for event in events).date(),
        period_end=close.index.max().date(),
    )
