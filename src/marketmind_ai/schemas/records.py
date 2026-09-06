"""Interfacce Pydantic condivise tra ingestion e strato di scrittura.

Rispecchia campo per campo il contratto fissato in
`Market Mind AI - Docs/Data Providers/00_schema_interfacce.md` (vault
Obsidian esterno al repo, percorso in CLAUDE.md). Ogni script di ingestion
deve produrre istanze di una di queste classi prima di scrivere su
Postgres — mai il payload grezzo direttamente: la validazione fallisce
subito ed esplicitamente se una fonte cambia formato, invece di lasciare
passare dati sporchi in silenzio.

Le interfacce usano identificatori naturali (`symbol`, `indicator`), non
l'`asset_id`/`news_event_id`/`company_event_id` interni: la risoluzione
all'id interno avviene nello strato di scrittura/upsert in `db/`, non negli
script di ingestion, che restano così disaccoppiati dagli id interni e più
facili da testare in isolamento.

`NewsEventRecord.raw_payload` e `CompanyEventRecord.raw_payload` restano
parte dell'interfaccia anche se le tabelle raffinate corrispondenti
(`market_data.t_news_events`/`market_data.t_company_events`) non portano
più quella colonna: lo strato di scrittura in `db/` instrada il payload
grezzo verso la tabella dedicata dello schema `raw`
(`raw.t_news_events_raw`/`raw.t_company_events_raw`, si veda
`db/models/raw.py`). Un singolo record validato produce quindi due insert
nella stessa transazione, non uno.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel


class AssetRecord(BaseModel):
    """Anagrafica asset, alimentata da yfinance (`.info`)."""

    symbol: str
    name: str
    sector: Optional[str] = None
    asset_type: str
    source: str
    fetched_at: datetime


class MarketPriceRecord(BaseModel):
    """Barra di prezzo intraday, alimentata da yfinance (`.history()`)."""

    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: str
    fetched_at: datetime


class NewsEventRecord(BaseModel):
    """Evento news, alimentato da GDELT (DOC API e Web NGrams) e Finnhub
    (`/company-news`).

    `symbol` nullable: GDELT non fornisce un mapping diretto articolo →
    ticker, il record viene comunque prodotto e l'entity linking lo risolve
    in un secondo momento (match sul `QUADGRAM` di Web NGrams).
    """

    source: str
    ts: datetime
    symbol: Optional[str] = None
    headline: str
    raw_payload: dict
    sentiment_score: Optional[float] = None
    url: str
    fetched_at: datetime


class MacroEventRecord(BaseModel):
    """Osservazione macro, alimentata da FRED (API ALFRED, vintage).

    `value` nullable: nullo se il dato non è ancora pubblicato (`"."` nella
    risposta grezza).
    """

    indicator: str
    ts: date
    value: Optional[float] = None
    source: str
    fetched_at: datetime


class CompanyEventRecord(BaseModel):
    """Evento societario, alimentato da Finnhub (`/calendar/earnings`) e FMP
    (bilanci, dividendi, split).
    """

    symbol: str
    ts: date
    event_type: Literal["earnings", "dividend", "split"]
    raw_payload: dict
    source: str
    fetched_at: datetime


class UniverseMemberRecord(BaseModel):
    """Appartenenza all'universo osservato, alimentata dalla pipeline
    `universe-csv` (seed statico `seeds/universe.csv`, non un'API esterna).

    `is_benchmark` è `True` solo per SPY: esclude l'asset dalla lista su cui
    gira il motore decisionale, non dall'ingestion.
    """

    symbol: str
    name: str
    is_benchmark: bool
    source: str
    fetched_at: datetime
