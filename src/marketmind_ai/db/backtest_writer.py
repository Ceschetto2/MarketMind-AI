"""Scrittura verso `decisions.t_backtest_results` per il Backtesting Engine.

File a sé rispetto a `decision_writer.py` (che resta scoped a `t_model_runs`/
`t_model_decisions`, scritte da `decision_engine/`): stesso schema
Postgres, consumatore diverso. Come `decision_writer.py`, nessun upsert —
ogni esecuzione del backtest produce una riga nuova, mai un update di una
precedente (`Market Mind AI - Docs/Backtest/00_motore_backtest.md`).

Parametri scalari, non un oggetto `BacktestMetrics` di `backtest/`: a
differenza di `llm/` (un vocabolario condiviso, senza dipendenze proprie su
`db/`, che `decision_writer.write_model_decision` importa senza problemi),
`backtest/engine.py` dipende esso stesso da `db/` — importare da lì
dentro `db/` inverterebbe la direzione di dipendenza del pacchetto, anche
se oggi non creerebbe un ciclo reale a runtime.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from marketmind_ai.db.models.decisions import BacktestResult


def write_backtest_result(
    session: Session,
    run_id: int,
    pnl: float,
    sharpe_ratio: float | None,
    max_drawdown: float | None,
    win_rate: float | None,
    period_start: date,
    period_end: date,
) -> int:
    row = BacktestResult(
        run_id=run_id,
        pnl=pnl,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        win_rate=win_rate,
        period_start=period_start,
        period_end=period_end,
    )
    session.add(row)
    session.flush()
    return row.backtest_id
