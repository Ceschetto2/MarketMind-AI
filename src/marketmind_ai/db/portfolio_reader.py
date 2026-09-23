"""Query verso `portfolio` per il Decision Engine.

File a sé rispetto a `context_reader.py`, che resta scoped a `market_data`:
la lettura dello stato di un portfolio è un accesso concettualmente diverso
(cash/posizioni, non dati di mercato) e a un altro schema Postgres.

Ogni funzione richiede un `portfolio_id` esplicito e legge solo quel
portfolio: nessuna qui interroga più portfolio insieme (a parte
`get_active_model_portfolios`/`get_due_model_portfolios`, che restituiscono
l'elenco su cui iterare, non lo stato di uno specifico) — l'isolamento tra
portfolio richiesto dal disegno (`Market Mind AI - Docs/Decision Engine/`)
si applica qui, non solo nel motore che chiama queste funzioni.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from marketmind_ai.db.models.market_data import Asset
from marketmind_ai.db.models.portfolio import Portfolio, PortfolioPosition, PortfolioWatchlistEntry


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


def get_due_model_portfolios(session: Session, as_of: datetime) -> list[Portfolio]:
    """Portfolio su cui il Decision Engine deve girare ORA: `'model'` +
    `is_active`, e `next_decision_at` nullo (mai schedulato — un portfolio
    creato ma non ancora inizializzato) oppure già scaduto (`<= as_of`).

    Distinta da `get_active_model_portfolios`: quella ignora la cadenza e
    restituisce ogni portfolio attivo indipendentemente da quando deve
    girare di nuovo; questa è la query che il timer condiviso usa davvero
    (`decision_engine.engine.run_due_decisions`) — un portfolio con una
    `next_decision_at` futura resta attivo ma non compare qui."""
    return list(
        session.execute(
            select(Portfolio).where(
                Portfolio.portfolio_type == "model",
                Portfolio.is_active.is_(True),
                or_(
                    Portfolio.next_decision_at.is_(None),
                    Portfolio.next_decision_at <= as_of,
                ),
            )
        ).scalars()
    )


def get_watchlist(session: Session, portfolio_id: int) -> list[Asset]:
    """Asset che questo portfolio osserva — lo scope su cui gira il loop
    settimanale ordinario per questo portfolio, popolato dal bootstrap
    (`decision_engine.engine.initialize_portfolio`). Distinta da
    `get_decision_universe` (`context_reader.py`), che resta l'universo
    intero, non filtrato per portfolio — usata solo dal bootstrap stesso
    per scegliere lo scope."""
    return list(
        session.execute(
            select(Asset)
            .join(PortfolioWatchlistEntry, PortfolioWatchlistEntry.asset_id == Asset.asset_id)
            .where(PortfolioWatchlistEntry.portfolio_id == portfolio_id)
        ).scalars()
    )
