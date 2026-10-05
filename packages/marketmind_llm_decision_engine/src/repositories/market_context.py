"""Letture su `market_data` per il contesto di una decisione.

Tutte con un `as_of` esplicito e nessun dato successivo (principio
no-look-ahead): finestre `(as_of - days_back, as_of]` per prezzi, news ed
eventi societari, e l'ultimo valore noto a `as_of` per ogni indicatore
macro (che cambia a cadenza propria, quindi non ha una finestra).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from marketmind_db.database import Transaction
from marketmind_db.models.market_data import (
    Asset,
    CompanyEvent,
    MacroEvent,
    MarketPrice,
    NewsEvent,
    UniverseMember,
)


@dataclass(frozen=True)
class AssetRef:
    """Un asset come valore, indipendente dalla sessione che l'ha letto."""

    asset_id: int
    symbol: str
    name: str
    sector: str | None
    asset_type: str

    @classmethod
    def of(cls, asset: Asset) -> AssetRef:
        return cls(asset.asset_id, asset.symbol, asset.name, asset.sector, asset.asset_type)


def _as_date(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


class MarketContextRepository:
    def __init__(self, tx: Transaction) -> None:
        self.tx = tx

    def prices(self, asset_id: int, *, as_of: datetime, days_back: int) -> list[MarketPrice]:
        """Barre orarie, dalla più vecchia; `limit=None` esplicito: una finestra
        di prezzi troncata in silenzio falserebbe il contesto."""
        return self.tx.repository(MarketPrice).select(
            where=[
                MarketPrice.asset_id == asset_id,
                MarketPrice.ts > as_of - timedelta(days=days_back),
                MarketPrice.ts <= as_of,
            ],
            order_by=("ts",),
            limit=None,
        )

    def news(self, asset_id: int, *, as_of: datetime, days_back: int, max_items: int) -> list[NewsEvent]:
        """Le più recenti prima, al massimo `max_items` (dimensione del prompt)."""
        return self.tx.repository(NewsEvent).select(
            where=[
                NewsEvent.asset_id == asset_id,
                NewsEvent.ts > as_of - timedelta(days=days_back),
                NewsEvent.ts <= as_of,
            ],
            order_by=("-ts",),
            limit=max_items,
        )

    def company_events(self, asset_id: int, *, as_of: datetime, days_back: int) -> list[CompanyEvent]:
        as_of_date = _as_date(as_of)
        return self.tx.repository(CompanyEvent).select(
            where=[
                CompanyEvent.asset_id == asset_id,
                CompanyEvent.ts > as_of_date - timedelta(days=days_back),
                CompanyEvent.ts <= as_of_date,
            ],
            order_by=("ts",),
            limit=None,
        )

    def latest_macro(self, *, as_of: datetime) -> list[MacroEvent]:
        """L'ultimo valore noto a `as_of` per ciascun indicatore (vintage
        ALFRED lato ingestion)."""
        return self.tx.repository(MacroEvent).latest_per(
            ("indicator",), order_by="ts", where=[MacroEvent.ts <= _as_date(as_of)], limit=None
        )

    def decision_universe(self) -> list[AssetRef]:
        """L'universo osservato esclusi i benchmark: i candidati per la
        watchlist di qualunque portfolio."""
        ids = self.tx.repository(UniverseMember).values(
            "asset_id", where={"is_benchmark": False}, limit=None
        )
        if not ids:
            return []
        assets = self.tx.repository(Asset).select(where={"asset_id": ids}, order_by=("symbol",), limit=None)
        return [AssetRef.of(a) for a in assets]
