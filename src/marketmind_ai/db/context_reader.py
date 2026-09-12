"""Query verso `market_data` per l'Historical Context Builder di `decision_engine/`.

Coerente col principio "solo `db/` parla con Postgres"
(`Market Mind AI - Docs/Architettura/00_struttura_cartelle.md`):
`decision_engine/` non costruisce SQLAlchemy proprio, chiama queste
funzioni. Ogni finestra temporale è un parametro esplicito, senza default
qui — i default (quanti giorni, quanti item) sono la logica di windowing,
responsabilità del context builder in `decision_engine/`, non di questo
strato di accesso.

Ogni query filtra `ts <= as_of`, mai oltre: è il meccanismo con cui il
principio no-look-ahead del progetto si applica concretamente. `as_of` è un
parametro esplicito (non `datetime.now()` preso qui dentro) apposta per
restare riutilizzabile da un futuro Backtesting Engine che dovrà simulare
`as_of` nel passato.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from marketmind_ai.db.models.market_data import (
    Asset,
    CompanyEvent,
    MacroEvent,
    MarketPrice,
    NewsEvent,
    UniverseMember,
)


def _as_date(as_of: datetime | date) -> date:
    return as_of.date() if isinstance(as_of, datetime) else as_of


def get_recent_prices(
    session: Session, asset_id: int, as_of: datetime, days_back: int
) -> list[MarketPrice]:
    """Barre di prezzo di `asset_id` negli ultimi `days_back` giorni fino a `as_of`."""
    since = as_of - timedelta(days=days_back)
    return list(
        session.execute(
            select(MarketPrice)
            .where(
                MarketPrice.asset_id == asset_id,
                MarketPrice.ts > since,
                MarketPrice.ts <= as_of,
            )
            .order_by(MarketPrice.ts)
        ).scalars()
    )


def get_recent_news(
    session: Session, asset_id: int, as_of: datetime, days_back: int, max_items: int
) -> list[NewsEvent]:
    """News di `asset_id` negli ultimi `days_back` giorni fino a `as_of`, le
    più recenti prima, capped a `max_items` per contenere il prompt."""
    since = as_of - timedelta(days=days_back)
    return list(
        session.execute(
            select(NewsEvent)
            .where(
                NewsEvent.asset_id == asset_id,
                NewsEvent.ts > since,
                NewsEvent.ts <= as_of,
            )
            .order_by(NewsEvent.ts.desc())
            .limit(max_items)
        ).scalars()
    )


def get_recent_company_events(
    session: Session, asset_id: int, as_of: datetime, days_back: int
) -> list[CompanyEvent]:
    """Eventi societari di `asset_id` negli ultimi `days_back` giorni fino a `as_of`."""
    as_of_date = _as_date(as_of)
    since = as_of_date - timedelta(days=days_back)
    return list(
        session.execute(
            select(CompanyEvent)
            .where(
                CompanyEvent.asset_id == asset_id,
                CompanyEvent.ts > since,
                CompanyEvent.ts <= as_of_date,
            )
            .order_by(CompanyEvent.ts)
        ).scalars()
    )


def get_latest_macro_events(session: Session, as_of: datetime) -> list[MacroEvent]:
    """L'ultimo valore noto per ciascun indicatore macro con `ts <= as_of`.

    Non una finestra temporale come le altre tre query: un indicatore come
    `UNRATE` cambia poco e a cadenza propria (mensile/trimestrale), quindi
    ha senso solo "l'ultimo valore conosciuto a questa data", non un range —
    coerente con l'uso di ALFRED lato ingestion (vintage, non serie rivista).
    """
    as_of_date = _as_date(as_of)
    latest_ts_per_indicator = (
        select(MacroEvent.indicator, func.max(MacroEvent.ts).label("max_ts"))
        .where(MacroEvent.ts <= as_of_date)
        .group_by(MacroEvent.indicator)
        .subquery()
    )
    return list(
        session.execute(
            select(MacroEvent).join(
                latest_ts_per_indicator,
                (MacroEvent.indicator == latest_ts_per_indicator.c.indicator)
                & (MacroEvent.ts == latest_ts_per_indicator.c.max_ts),
            )
        ).scalars()
    )


def get_decision_universe(session: Session) -> list[Asset]:
    """Asset su cui gira davvero il motore decisionale — l'universo osservato
    esclusi i benchmark (`is_benchmark = false`); l'esclusione è un filtro
    a valle dell'ingestion, non dell'ingestion stessa (`t_universe_members`
    include SPY per le sue stesse esigenze di dati)."""
    return list(
        session.execute(
            select(Asset)
            .join(UniverseMember, UniverseMember.asset_id == Asset.asset_id)
            .where(UniverseMember.is_benchmark.is_(False))
        ).scalars()
    )
