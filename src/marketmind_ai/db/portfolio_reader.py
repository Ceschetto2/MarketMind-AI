"""Query verso `portfolio` per il Decision Engine.

File a sé rispetto a `context_reader.py`, che resta scoped a `market_data`:
la lettura dello stato di un portfolio è un accesso concettualmente diverso
(cash/posizioni, non dati di mercato) e a un altro schema Postgres.

Ogni funzione richiede un `portfolio_id` esplicito e legge solo quel
portfolio: nessuna qui interroga più portfolio insieme (a parte
`get_active_model_portfolios`, che restituisce l'elenco su cui iterare, non
lo stato di uno specifico) — l'isolamento tra portfolio richiesto dal
disegno (`Market Mind AI - Docs/Decision Engine/`) si applica qui, non solo
nel motore che chiama queste funzioni.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from marketmind_ai.db.models.portfolio import Portfolio, PortfolioPosition


def get_portfolio(session: Session, portfolio_id: int) -> Portfolio:
    return session.execute(
        select(Portfolio).where(Portfolio.portfolio_id == portfolio_id)
    ).scalar_one()


def get_portfolio_positions(session: Session, portfolio_id: int) -> list[PortfolioPosition]:
    """Posizioni correnti, con `.asset` già caricato (`joinedload`): il
    context builder legge `.asset.symbol` senza una query separata per
    riga."""
    return list(
        session.execute(
            select(PortfolioPosition)
            .options(joinedload(PortfolioPosition.asset))
            .where(PortfolioPosition.portfolio_id == portfolio_id)
        ).scalars()
    )


def get_active_model_portfolios(session: Session) -> list[Portfolio]:
    """Portfolio su cui il Decision Engine deve girare in questo ciclo —
    `portfolio_type='model'` e `is_active`; il benchmark (buy & hold, mai
    una chiamata LLM) resta escluso per disegno."""
    return list(
        session.execute(
            select(Portfolio).where(
                Portfolio.portfolio_type == "model",
                Portfolio.is_active.is_(True),
            )
        ).scalars()
    )
