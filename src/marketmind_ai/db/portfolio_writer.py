"""Scrittura verso `portfolio` per il bootstrap di un portfolio.

File a sé rispetto a `portfolio_reader.py` (letture) e a `decision_writer.py`
(scritture verso lo schema `decisions`): questa è l'unica scrittura verso
`portfolio` che `decision_engine/` fa oggi — mai su `t_portfolios`/
`t_portfolio_positions`, che restano responsabilità del Backtesting Engine.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.orm import Session

from marketmind_ai.db.models.portfolio import PortfolioWatchlistEntry


def write_watchlist(
    session: Session, portfolio_id: int, asset_ids: list[int], added_at: datetime
) -> None:
    """Sostituisce la watchlist di un portfolio con l'insieme dato.

    Un replace totale, non un merge incrementale: il bootstrap
    (`decision_engine.engine.initialize_portfolio`) ripensa lo scope da zero
    ogni volta che viene rilanciato, non lo estende. Una watchlist vuota
    (`asset_ids=[]`) è un caso valido — svuota lo scope, non un errore.
    """
    session.execute(
        delete(PortfolioWatchlistEntry).where(
            PortfolioWatchlistEntry.portfolio_id == portfolio_id
        )
    )
    for asset_id in asset_ids:
        session.add(
            PortfolioWatchlistEntry(portfolio_id=portfolio_id, asset_id=asset_id, added_at=added_at)
        )
    session.flush()
