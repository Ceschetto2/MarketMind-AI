"""Sink: scrittura dei record Pydantic sulle tabelle di `market_data`/`raw`.

Un sink per tipo di record, tutti sopra `TableRepository` (quindi con i
safeguard di `marketmind_db`) e sulla transazione che ricevono: la
risoluzione `symbol` → `asset_id`, gli upsert idempotenti e il payload
grezzo nella stessa transazione della riga raffinata vivono qui, non nelle
singole pipeline.

Ogni `write()` restituisce il numero di righe della tabella principale
inserite o aggiornate.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from typing import Any

from marketmind_db.database import Transaction
from marketmind_db.models.market_data import (
    Asset,
    CompanyEvent,
    MacroEvent,
    MarketPrice,
    NewsEvent,
    UniverseMember,
)
from marketmind_db.models.raw import CompanyEventRaw, NewsEventRaw
from marketmind_pipelines.records import (
    AssetRecord,
    CompanyEventRecord,
    MacroEventRecord,
    MarketPriceRecord,
    NewsEventRecord,
    UniverseMemberRecord,
)


class AssetNotFoundError(LookupError):
    """Uno o più `symbol` non presenti in `market_data.t_assets`.

    Il sink non crea al volo un asset incompleto: le interfacce diverse da
    `AssetRecord`/`UniverseMemberRecord` non portano i campi obbligatori
    (`name`, `asset_type`). Per i simboli dell'universo non può succedere
    (`t_universe_members.asset_id` è una FK verso `t_assets`).
    """


def resolve_asset_ids(tx: Transaction, symbols: Iterable[str]) -> dict[str, int]:
    """`symbol` → `asset_id` per tutti i simboli richiesti, in una query."""
    wanted = sorted(set(symbols))
    if not wanted:
        return {}
    assets = tx.repository(Asset).select(where={"symbol": wanted}, limit=None)
    resolved = {a.symbol: a.asset_id for a in assets}
    missing = [s for s in wanted if s not in resolved]
    if missing:
        raise AssetNotFoundError(
            f"symbol non presenti in market_data.t_assets: {', '.join(missing)} — "
            "esegui prima universe-csv o yfinance-assets"
        )
    return resolved


class RecordSink[RecordT](ABC):
    def __init__(self, tx: Transaction) -> None:
        self.tx = tx

    @abstractmethod
    def write(self, records: Sequence[RecordT]) -> int: ...


class AssetSink(RecordSink[AssetRecord]):
    """Anagrafica: l'aggiornamento è lo scopo stesso di `yfinance-assets`,
    quindi l'upsert sovrascrive nome/settore/tipo."""

    def write(self, records: Sequence[AssetRecord]) -> int:
        return self.tx.repository(Asset).upsert(
            [r.model_dump() for r in records], conflict_on=("symbol",)
        )


class UniverseMemberSink(RecordSink[UniverseMemberRecord]):
    """Crea l'asset se manca, senza aggiornarne uno esistente (un nome più
    fresco scritto da `yfinance-assets` non va sovrascritto dal CSV di
    seed), poi aggiorna la membership."""

    def write(self, records: Sequence[UniverseMemberRecord]) -> int:
        if not records:
            return 0
        self.tx.repository(Asset).upsert(
            [
                r.model_dump(include={"symbol", "name", "sector", "asset_type", "source", "fetched_at"})
                for r in records
            ],
            conflict_on=("symbol",),
            update=(),
        )
        ids = resolve_asset_ids(self.tx, (r.symbol for r in records))
        return self.tx.repository(UniverseMember).upsert(
            [
                {
                    "asset_id": ids[r.symbol],
                    "is_benchmark": r.is_benchmark,
                    "source": r.source,
                    "fetched_at": r.fetched_at,
                }
                for r in records
            ],
            conflict_on=("asset_id",),
        )


class MarketPriceSink(RecordSink[MarketPriceRecord]):
    def write(self, records: Sequence[MarketPriceRecord]) -> int:
        ids = resolve_asset_ids(self.tx, (r.symbol for r in records))
        return self.tx.repository(MarketPrice).upsert(
            [{"asset_id": ids[r.symbol]} | r.model_dump(exclude={"symbol"}) for r in records],
            conflict_on=("asset_id", "ts", "source"),
        )


class MacroEventSink(RecordSink[MacroEventRecord]):
    """Un valore rivisto (da `None` a un numero, o una revisione) sovrascrive
    il precedente: con ALFRED è sempre il più aggiornato noto al momento
    della query, mai quello di un vintage passato."""

    def write(self, records: Sequence[MacroEventRecord]) -> int:
        return self.tx.repository(MacroEvent).upsert(
            [r.model_dump() for r in records], conflict_on=("indicator", "ts")
        )


class NewsEventSink(RecordSink[NewsEventRecord]):
    """Riga raffinata (upsert su `url`) e payload grezzo in `raw`, nella
    stessa transazione. `source`/`ts` restano quelli della prima scrittura."""

    def write(self, records: Sequence[NewsEventRecord]) -> int:
        if not records:
            return 0
        ids = resolve_asset_ids(self.tx, (r.symbol for r in records if r.symbol is not None))
        written = self.tx.repository(NewsEvent).upsert_returning(
            [
                {"asset_id": ids.get(r.symbol) if r.symbol else None}
                | r.model_dump(exclude={"symbol", "raw_payload"})
                for r in records
            ],
            conflict_on=("url",),
            update=("asset_id", "headline", "sentiment_score", "fetched_at"),
            returning=("news_event_id", "url"),
        )
        id_by_url = {row["url"]: row["news_event_id"] for row in written}
        _write_raw(
            self.tx,
            NewsEventRaw,
            "news_event_id",
            {id_by_url[r.url]: r for r in records},
        )
        return len(written)


class CompanyEventSink(RecordSink[CompanyEventRecord]):
    """Riga raffinata (upsert su `(asset_id, ts, event_type, source)`: fonti
    diverse sullo stesso evento convivono come righe distinte) con le sei
    colonne identificative FMP, e payload grezzo in `raw`."""

    _KEY = ("asset_id", "ts", "event_type", "source")

    def write(self, records: Sequence[CompanyEventRecord]) -> int:
        if not records:
            return 0
        ids = resolve_asset_ids(self.tx, (r.symbol for r in records))
        rows = [
            {"asset_id": ids[r.symbol]} | r.model_dump(exclude={"symbol", "raw_payload"})
            for r in records
        ]
        written = self.tx.repository(CompanyEvent).upsert_returning(
            rows,
            conflict_on=self._KEY,
            update=(
                "fetched_at",
                "fiscal_year",
                "period",
                "reported_currency",
                "cik",
                "filing_date",
                "accepted_date",
            ),
            returning=("company_event_id", *self._KEY),
        )
        id_by_key = {tuple(row[k] for k in self._KEY): row["company_event_id"] for row in written}
        _write_raw(
            self.tx,
            CompanyEventRaw,
            "company_event_id",
            {id_by_key[tuple(row[k] for k in self._KEY)]: r for row, r in zip(rows, records)},
        )
        return len(written)


def _write_raw(tx: Transaction, model: Any, id_column: str, records_by_id: dict[int, Any]) -> None:
    """Payload grezzo 1:1 con la riga raffinata: a parità di id vince
    l'ultimo record, coerente con l'upsert della raffinata."""
    tx.repository(model).upsert(
        [
            {
                id_column: event_id,
                "source": record.source,
                "fetched_at": record.fetched_at,
                "raw_payload": record.raw_payload,
            }
            for event_id, record in records_by_id.items()
        ],
        conflict_on=(id_column,),
        update=("raw_payload", "fetched_at"),
    )
