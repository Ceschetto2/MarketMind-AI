"""Query verso `portfolio` per il Backtesting Engine.

Isola i trade eseguiti in un run specifico (`Market Mind AI - Docs/Backtest/
00_motore_backtest.md`): `get_position_events()` legge
`t_portfolio_position_snapshots` filtrata per `(portfolio_id, run_id)` — il
collegamento richiede il fix di `db/session.py` (`track_model_run`),
sistemato lavorando su questo modulo (`Market Mind AI - Docs/tasks/
2026-09-14-run-id-guc-linkage.md`): prima nessuno snapshot lo portava,
sempre NULL.

Il prezzo di un asset non è responsabilità di questo file:
`get_recent_prices` (`db/context_reader.py`) è già la query giusta,
riusata invece di duplicarla — l'Historical Context Builder e il
Backtesting Engine hanno lo stesso bisogno (storico prezzi di un asset fino
a un istante), solo con una finestra diversa.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from marketmind_ai.db.models.portfolio import PortfolioPositionSnapshot


def get_position_events(
    session: Session, portfolio_id: int, run_id: int
) -> list[PortfolioPositionSnapshot]:
    """Gli eventi di posizione (INSERT/UPDATE/DELETE) originati da questo
    run per questo portfolio, in ordine cronologico — con `.asset` già
    caricato (`joinedload`, stesso pattern di
    `portfolio_reader.get_portfolio_positions`). Una lista vuota significa
    che questo run non ha eseguito alcun trade (solo HOLD, o nessuna
    decisione), non un errore: la distinzione è responsabilità del
    chiamante (`backtest/engine.py`)."""
    return list(
        session.execute(
            select(PortfolioPositionSnapshot)
            .options(joinedload(PortfolioPositionSnapshot.asset))
            .where(
                PortfolioPositionSnapshot.portfolio_id == portfolio_id,
                PortfolioPositionSnapshot.run_id == run_id,
            )
            .order_by(PortfolioPositionSnapshot.changed_at)
        ).scalars()
    )
