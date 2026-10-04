"""Pipeline `yfinance-prices`: prezzi intraday orari per l'universo osservato.

Cadenza oraria (timer `marketmind-ingest-yfinance-prices.timer`). Per ogni
ticker scarica l'ultimo giorno di barre orarie (`period="1d",
interval="60m"`): nessun backfill storico profondo, l'overlap tra run
consecutivi copre eventuali run saltati senza tracciare un "ultimo
timestamp visto" per ticker, e l'upsert sulla chiave `(asset_id, ts,
source)` deduplica.

yfinance non pubblica un rate limit ufficiale (~360 richieste/ora osservate,
non garantite): pausa casuale tra i ticker e retry con backoff, perché gli
errori di yfinance non sono classificabili per status code come quelli di
un'API HTTP.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from marketmind_pipelines.base import PerSymbolPipeline
from marketmind_pipelines.http import call_with_retry
from marketmind_pipelines.records import MarketPriceRecord
from marketmind_pipelines.sinks import MarketPriceSink

logger = logging.getLogger(__name__)

SOURCE = "yfinance"


def rows_to_records(symbol: str, history: pd.DataFrame, fetched_at: datetime) -> list[MarketPriceRecord]:
    return [
        MarketPriceRecord(
            symbol=symbol,
            ts=ts.to_pydatetime(),
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            volume=int(row["Volume"]),
            source=SOURCE,
            fetched_at=fetched_at,
        )
        for ts, row in history.iterrows()
    ]


class YFinancePricesPipeline(PerSymbolPipeline[pd.DataFrame, MarketPriceRecord]):
    name = "yfinance-prices"
    audit_source = "yfinance"
    target_table = "market_data.t_market_prices"
    sink = MarketPriceSink
    delay_between_targets = (1.0, 3.0)

    def extract(self, target: str) -> pd.DataFrame:
        return call_with_retry(
            lambda: yf.Ticker(target).history(period="1d", interval="60m"), sleep=self._sleep
        )

    def transform(self, target: str, raw: pd.DataFrame) -> list[MarketPriceRecord]:
        if raw.empty:
            logger.warning("nessuna barra restituita per %s", target)
            return []
        return rows_to_records(target, raw, datetime.now(timezone.utc))
