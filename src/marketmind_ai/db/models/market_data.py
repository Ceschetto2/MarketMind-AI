"""Tabelle dello schema `market_data`.

Le tabelle di ingestion (`t_assets`, `t_market_prices`, `t_news_events`,
`t_macro_events`, `t_company_events`) non conoscono strutturalmente le fonti
dati: la provenienza vive solo in `source`/`fetched_at`/`raw_payload`
(colonne presenti su ognuna) così da poter sostituire o aggiungere fonti
senza toccare lo schema.

NOTA: le colonne oltre a quelle esplicitamente decise in CLAUDE.md (schema
dati, PK di `t_market_prices`) sono una prima bozza ragionevole in assenza
del documento ER (`Market Mind AI - Docs/Architettura/01_schema_dati_er.md`,
non presente in questo checkout) e delle interfacce Pydantic
(`00_schema_interfacce.md`) — da rivedere non appena quei documenti sono
disponibili.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from marketmind_ai.db.base import Base

SCHEMA = "market_data"


class Asset(Base):
    """Anagrafica dei ~500 asset dell'universo S&P 500.

    `symbol` è l'identificatore naturale usato dalle interfacce di
    ingestion; `asset_id` è l'id interno a cui `symbol` viene risolto nello
    strato di scrittura/upsert (mai negli script di ingestion stessi).
    """

    __tablename__ = "t_assets"
    __table_args__ = {"schema": SCHEMA}

    asset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    exchange: Mapped[str | None] = mapped_column(String(20))
    sector: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(String(10))
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default="now()"
    )
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    prices: Mapped[list["MarketPrice"]] = relationship(back_populates="asset")


class MarketPrice(Base):
    """Prezzi intraday (granularità oraria), hypertable su `ts`.

    PK estesa a `(asset_id, ts, source)` — decisione del 29-08-26: più fonti
    possono riportare la stessa barra oraria per lo stesso asset e vanno
    conservate entrambe, non deduplicate silenziosamente.
    """

    __tablename__ = "t_market_prices"
    __table_args__ = {"schema": SCHEMA}

    asset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.t_assets.asset_id"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    source: Mapped[str] = mapped_column(String(50), primary_key=True)

    open: Mapped[float | None] = mapped_column(Numeric(18, 6))
    high: Mapped[float | None] = mapped_column(Numeric(18, 6))
    low: Mapped[float | None] = mapped_column(Numeric(18, 6))
    close: Mapped[float | None] = mapped_column(Numeric(18, 6))
    volume: Mapped[int | None] = mapped_column(BigInteger)

    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)

    asset: Mapped["Asset"] = relationship(back_populates="prices")


class NewsEvent(Base):
    """Eventi news (GDELT Web NGrams in prima battuta).

    `asset_id` nullable: l'entity linking (match nome azienda/ticker sul
    QUADGRAM di Web NGrams) può non risolvere a un singolo asset.
    """

    __tablename__ = "t_news_events"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.t_assets.asset_id")
    )
    event_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    tone: Mapped[float | None] = mapped_column(Numeric(9, 4))
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_t_news_events_asset_id_event_ts", "asset_id", "event_ts"),
        {"schema": SCHEMA},
    )


class MacroEvent(Base):
    """Osservazioni macro da FRED, lette via API ALFRED (vintage).

    `vintage_date` è la data alla quale il valore era effettivamente noto
    (principio no-look-ahead), distinta da `event_ts` che è il periodo a
    cui l'osservazione si riferisce.
    """

    __tablename__ = "t_macro_events"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    indicator_code: Mapped[str] = mapped_column(String(50), nullable=False)
    event_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    vintage_date: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    value: Mapped[float | None] = mapped_column(Numeric(20, 8))
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_t_macro_events_indicator_code_event_ts", "indicator_code", "event_ts"),
        {"schema": SCHEMA},
    )


class CompanyEvent(Base):
    """Eventi societari (earnings, dividendi, split, ...)."""

    __tablename__ = "t_company_events"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.t_assets.asset_id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_t_company_events_asset_id_event_ts", "asset_id", "event_ts"),
        {"schema": SCHEMA},
    )
