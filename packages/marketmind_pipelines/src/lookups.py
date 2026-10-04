"""Letture di supporto delle pipeline: su quali asset girare.

Tutte con `limit=None` esplicito: il limite di lettura di default di
`TableRepository` troncherebbe in silenzio un universo più grande di quel
limite (l'obiettivo è ~500 asset).
"""

from __future__ import annotations

from datetime import date

from marketmind_db.database import Transaction
from marketmind_db.models.market_data import Asset, CompanyEvent, UniverseMember


def _universe(tx: Transaction) -> list[Asset]:
    ids = tx.repository(UniverseMember).values("asset_id", limit=None)
    if not ids:
        return []
    return tx.repository(Asset).select(where={"asset_id": ids}, order_by=("symbol",), limit=None)


def universe_symbols(tx: Transaction) -> list[str]:
    """Ticker dell'universo osservato (`t_universe_members`), SPY incluso:
    anche il benchmark ha bisogno dei propri dati; l'esclusione dal motore
    decisionale è un filtro a valle, non dell'ingestion."""
    return [a.symbol for a in _universe(tx)]


def universe_assets(tx: Transaction) -> list[tuple[str, str]]:
    """`(symbol, name)` dell'universo, per l'entity linking testuale."""
    return [(a.symbol, a.name) for a in _universe(tx)]


def symbols_with_recent_company_events(
    tx: Transaction, *, source: str, event_type: str, since: date
) -> list[str]:
    """Ticker con almeno un evento `source`/`event_type` da `since` in poi."""
    ids = tx.repository(CompanyEvent).values(
        "asset_id",
        where=[
            CompanyEvent.source == source,
            CompanyEvent.event_type == event_type,
            CompanyEvent.ts >= since,
        ],
        distinct=True,
        limit=None,
    )
    if not ids:
        return []
    return tx.repository(Asset).values("symbol", where={"asset_id": ids}, order_by=("symbol",), limit=None)
