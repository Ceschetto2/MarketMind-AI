"""Strato di scrittura condiviso tra tutte le pipeline di ingestion.

Due responsabilità, entrambe deliberatamente fuori dagli script di
ingestion (`Market Mind AI - Docs/db/01_schema_dati_er.md`, § principio di
disegno): la risoluzione `symbol` → `asset_id` e l'upsert idempotente verso
Postgres, così gli script di ingestion restano disaccoppiati dagli id
interni e parlano solo le interfacce Pydantic di `schemas/`.

`ingestion_run()` traccia ogni esecuzione in `audit.t_ingestion_runs` in una
transazione **separata** da quella che scrive i dati veri e propri: se la
scrittura dati fallisce a metà, l'audit trail deve comunque registrare
`status='failed'` con l'errore, non sparire insieme al rollback dei dati.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from marketmind_ai.db.models.audit import IngestionRun
from marketmind_ai.db.models.market_data import (
    Asset,
    CompanyEvent,
    MarketPrice,
    NewsEvent,
    UniverseMember,
)
from marketmind_ai.db.models.raw import CompanyEventRaw, NewsEventRaw
from marketmind_ai.db.session import get_session
from marketmind_ai.schemas import (
    CompanyEventRecord,
    MarketPriceRecord,
    NewsEventRecord,
    UniverseMemberRecord,
)


class AssetNotFoundError(LookupError):
    """`symbol` non presente in `market_data.t_assets`.

    Sollevato invece di creare al volo una riga `Asset` incompleta: le
    interfacce di ingestion diverse da `AssetRecord` non portano i campi
    obbligatori (`name`, `asset_type`) per popolarla correttamente. La
    pipeline chiamante deve gestire il caso (skip + log), non affidarsi
    allo strato di scrittura per inventare dati mancanti.
    """


def resolve_asset_id(session: Session, symbol: str) -> int:
    """Risolve `symbol` all'`asset_id` interno.

    Solleva `AssetNotFoundError` se l'asset non esiste ancora — significa
    che `yfinance-assets` o `universe-csv` non hanno ancora scritto quel
    ticker in `t_assets`, non è compito di questa funzione rimediare.
    """
    asset_id = session.execute(
        select(Asset.asset_id).where(Asset.symbol == symbol)
    ).scalar_one_or_none()
    if asset_id is None:
        raise AssetNotFoundError(
            f"symbol={symbol!r} non trovato in market_data.t_assets — "
            "esegui prima la pipeline yfinance-assets o universe-csv."
        )
    return asset_id


def upsert_market_price(session: Session, asset_id: int, record: MarketPriceRecord) -> None:
    """Upsert idempotente su `market_data.t_market_prices`.

    Chiave `(asset_id, ts, source)`: una nuova ingestion della stessa barra
    aggiorna i prezzi invece di duplicare la riga (utile se uno script
    viene rieseguito su una finestra temporale già coperta).
    """
    stmt = pg_insert(MarketPrice).values(
        asset_id=asset_id,
        ts=record.ts,
        source=record.source,
        open=record.open,
        high=record.high,
        low=record.low,
        close=record.close,
        volume=record.volume,
        fetched_at=record.fetched_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[MarketPrice.asset_id, MarketPrice.ts, MarketPrice.source],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
            "fetched_at": stmt.excluded.fetched_at,
        },
    )
    session.execute(stmt)


def resolve_or_create_asset(session: Session, record: UniverseMemberRecord) -> int:
    """Come `resolve_asset_id`, ma crea la riga in `t_assets` se manca.

    Solo per pipeline che portano abbastanza dati per farlo (oggi solo
    `universe-csv`, via `UniverseMemberRecord`, l'unica interfaccia con
    `asset_type`): non aggiorna un asset già esistente, anche se il record
    porta un `name`/`sector` diverso — evitare che `universe-csv` sovrascriva
    dati più freschi scritti da `yfinance-assets` non è nel suo compito.
    """
    asset_id = session.execute(
        select(Asset.asset_id).where(Asset.symbol == record.symbol)
    ).scalar_one_or_none()
    if asset_id is not None:
        return asset_id

    asset = Asset(
        symbol=record.symbol,
        name=record.name,
        sector=record.sector,
        asset_type=record.asset_type,
        source=record.source,
        fetched_at=record.fetched_at,
    )
    session.add(asset)
    session.flush()
    return asset.asset_id


def upsert_universe_member(
    session: Session, asset_id: int, record: UniverseMemberRecord
) -> None:
    """Upsert idempotente su `market_data.t_universe_members` (PK `asset_id`)."""
    stmt = pg_insert(UniverseMember).values(
        asset_id=asset_id,
        is_benchmark=record.is_benchmark,
        source=record.source,
        fetched_at=record.fetched_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[UniverseMember.asset_id],
        set_={
            "is_benchmark": stmt.excluded.is_benchmark,
            "source": stmt.excluded.source,
            "fetched_at": stmt.excluded.fetched_at,
        },
    )
    session.execute(stmt)


def write_news_event(
    session: Session, asset_id: int | None, record: NewsEventRecord
) -> None:
    """Upsert su `market_data.t_news_events` (chiave naturale: `url`, `0006`)
    + payload grezzo in `raw.t_news_events_raw` — un record validato produce
    due insert nella stessa transazione, come da `00_schema_interfacce.md`.
    """
    stmt = pg_insert(NewsEvent).values(
        asset_id=asset_id,
        source=record.source,
        ts=record.ts,
        headline=record.headline,
        url=record.url,
        sentiment_score=record.sentiment_score,
        fetched_at=record.fetched_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[NewsEvent.url],
        set_={
            "asset_id": stmt.excluded.asset_id,
            "headline": stmt.excluded.headline,
            "sentiment_score": stmt.excluded.sentiment_score,
            "fetched_at": stmt.excluded.fetched_at,
        },
    ).returning(NewsEvent.news_event_id)
    news_event_id = session.execute(stmt).scalar_one()

    raw_stmt = pg_insert(NewsEventRaw).values(
        news_event_id=news_event_id,
        source=record.source,
        fetched_at=record.fetched_at,
        raw_payload=record.raw_payload,
    )
    raw_stmt = raw_stmt.on_conflict_do_update(
        index_elements=[NewsEventRaw.news_event_id],
        set_={
            "raw_payload": raw_stmt.excluded.raw_payload,
            "fetched_at": raw_stmt.excluded.fetched_at,
        },
    )
    session.execute(raw_stmt)


def write_company_event(
    session: Session, asset_id: int, record: CompanyEventRecord
) -> None:
    """Upsert su `market_data.t_company_events` (chiave naturale:
    `(asset_id, ts, event_type)`, `0006`) + payload grezzo in
    `raw.t_company_events_raw`.
    """
    stmt = pg_insert(CompanyEvent).values(
        asset_id=asset_id,
        ts=record.ts,
        event_type=record.event_type,
        source=record.source,
        fetched_at=record.fetched_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[CompanyEvent.asset_id, CompanyEvent.ts, CompanyEvent.event_type],
        set_={
            "source": stmt.excluded.source,
            "fetched_at": stmt.excluded.fetched_at,
        },
    ).returning(CompanyEvent.company_event_id)
    company_event_id = session.execute(stmt).scalar_one()

    raw_stmt = pg_insert(CompanyEventRaw).values(
        company_event_id=company_event_id,
        source=record.source,
        fetched_at=record.fetched_at,
        raw_payload=record.raw_payload,
    )
    raw_stmt = raw_stmt.on_conflict_do_update(
        index_elements=[CompanyEventRaw.company_event_id],
        set_={
            "raw_payload": raw_stmt.excluded.raw_payload,
            "fetched_at": raw_stmt.excluded.fetched_at,
        },
    )
    session.execute(raw_stmt)


@dataclass
class IngestionRunTracker:
    """Handle mutabile restituito da `ingestion_run()` per accumulare `rows_written`."""

    run_id: int
    rows_written: int = 0


@contextmanager
def ingestion_run(source: str, target_table: str) -> Iterator[IngestionRunTracker]:
    """Traccia un'esecuzione di pipeline in `audit.t_ingestion_runs`.

    Uso tipico::

        with ingestion_run("yfinance", "market_data.t_market_prices") as run:
            with get_session() as session:
                ...
                run.rows_written += 1

    La riga passa a `status='running'` subito (commit immediato, sessione
    propria), poi a `success`/`failed` all'uscita del blocco — in una
    sessione propria anche in caso di eccezione, cosicché un fallimento
    nella transazione dati del chiamante non si porti via anche il record
    di audit del fallimento stesso.
    """
    with get_session() as session:
        run = IngestionRun(
            source=source,
            target_table=target_table,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        session.add(run)
        session.flush()
        run_id = run.run_id

    tracker = IngestionRunTracker(run_id=run_id)
    try:
        yield tracker
    except Exception as exc:
        with get_session() as session:
            session.execute(
                update(IngestionRun)
                .where(IngestionRun.run_id == run_id)
                .values(
                    status="failed",
                    finished_at=datetime.now(timezone.utc),
                    rows_written=tracker.rows_written,
                    error_message=str(exc)[:2000],
                )
            )
        raise
    else:
        with get_session() as session:
            session.execute(
                update(IngestionRun)
                .where(IngestionRun.run_id == run_id)
                .values(
                    status="success",
                    finished_at=datetime.now(timezone.utc),
                    rows_written=tracker.rows_written,
                )
            )
