"""Tabelle dello schema `market_data`.

Rispecchia colonna per colonna l'`erDiagram` confermato in
`Market Mind AI - Docs/Architettura/01_schema_dati_er.md` (vault Obsidian
esterno al repo, percorso in CLAUDE.md). Le tabelle di ingestion (`t_assets`,
`t_market_prices`, `t_news_events`, `t_macro_events`, `t_company_events`)
non conoscono strutturalmente le fonti dati: la provenienza vive solo in
`source`/`fetched_at`, e `raw_payload` in JSONB dove serve preservare il
payload originale (non su `t_market_prices`, già completamente tipizzata).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Double,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from marketmind_ai.db.base import Base

SCHEMA = "market_data"


class Asset(Base):
    """Anagrafica dei ~500 asset dell'universo S&P 500 (alimentata da yfinance `.info`).

    `symbol` è l'identificatore naturale usato dalle interfacce di
    ingestion; `asset_id` è l'id interno a cui `symbol` viene risolto nello
    strato di scrittura/upsert (mai negli script di ingestion stessi).
    """

    __tablename__ = "t_assets"
    __table_args__ = {"schema": SCHEMA}

    asset_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    sector: Mapped[str | None] = mapped_column(Text)
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    prices: Mapped[list["MarketPrice"]] = relationship(back_populates="asset")


class MarketPrice(Base):
    """Prezzi intraday (granularità oraria), hypertable su `ts`.

    PK estesa a `(asset_id, ts, source)` — decisione del 29-08-26, in vista
    di una seconda fonte prezzi per l'intraday (per ora solo yfinance).
    """

    __tablename__ = "t_market_prices"
    __table_args__ = {"schema": SCHEMA}

    asset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.t_assets.asset_id"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), primary_key=True)
    source: Mapped[str] = mapped_column(String(50), primary_key=True)

    open: Mapped[float] = mapped_column(Double, nullable=False)
    high: Mapped[float] = mapped_column(Double, nullable=False)
    low: Mapped[float] = mapped_column(Double, nullable=False)
    close: Mapped[float] = mapped_column(Double, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    asset: Mapped["Asset"] = relationship(back_populates="prices")


class NewsEvent(Base):
    """Eventi news (GDELT DOC API / Web NGrams, Finnhub `/company-news`).

    `asset_id` nullable: GDELT non fornisce un mapping diretto articolo →
    ticker, la riga viene scritta comunque e l'entity linking la aggiorna
    in un secondo momento (match sul `QUADGRAM` di Web NGrams).
    """

    __tablename__ = "t_news_events"

    news_event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.t_assets.asset_id")
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sentiment_score: Mapped[float | None] = mapped_column(Double)
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("ib_news_events_asset_ts", "asset_id", "ts"),
        Index(
            "ib_news_events_unlinked",
            "ts",
            postgresql_where=text("asset_id IS NULL"),
        ),
        {"schema": SCHEMA},
    )


class MacroEvent(Base):
    """Osservazioni macro da FRED, lette via API ALFRED (vintage, no-look-ahead).

    Chiave naturale `(indicator, ts)`, senza id surrogato: un indicatore
    macro come `UNRATE` non appartiene a un singolo asset.
    """

    __tablename__ = "t_macro_events"
    __table_args__ = {"schema": SCHEMA}

    indicator: Mapped[str] = mapped_column(Text, primary_key=True)
    ts: Mapped[date] = mapped_column(primary_key=True)
    value: Mapped[float | None] = mapped_column(Double)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )


class CompanyEvent(Base):
    """Eventi societari (Finnhub `/calendar/earnings`, FMP bilanci/dividendi/split)."""

    __tablename__ = "t_company_events"

    company_event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.t_assets.asset_id"), nullable=False
    )
    ts: Mapped[date] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('earnings', 'dividend', 'split')", name="event_type"
        ),
        Index("ib_company_events_asset_ts", "asset_id", "ts"),
        {"schema": SCHEMA},
    )
