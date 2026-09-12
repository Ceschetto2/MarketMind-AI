"""Pipeline `finnhub-news`: company news recenti per l'universo osservato.

Entry point standalone, invocato dal container Quadlet
`marketmind-ingest-finnhub-news.container` — cadenza oraria (già decisa,
vedi `Market Mind AI - Docs/pipelines/01_trigger_e_scheduling.md`).

Per ciascun ticker dell'universo, `GET /company-news` su una finestra di 48h
(overlap di sicurezza: l'upsert su `url` in `write_news_event` deduplica se
un articolo ricompare in due run consecutivi). Piano free ~60 richieste/min,
26 ticker per run restano ben dentro il limite anche con un piccolo delay.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta, timezone

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from marketmind_ai.config import get_api_key
from marketmind_ai.db.session import get_session
from marketmind_ai.db.writer import (
    AssetNotFoundError,
    get_universe_symbols,
    ingestion_run,
    resolve_asset_id,
    write_news_event,
)
from marketmind_ai.schemas import NewsEventRecord
from marketmind_ai.utils.logging_config import configure_logging

logger = logging.getLogger(__name__)

SOURCE = "Finnhub"  # valore di NewsEventRecord.source (00_schema_interfacce.md)
AUDIT_SOURCE = "finnhub"  # valore ammesso da ck_t_ingestion_runs_source (minuscolo)
TARGET_TABLE = "market_data.t_news_events"

_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"
_WINDOW_HOURS = 48
_DELAY_BETWEEN_SYMBOLS = (0.2, 0.5)


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _fetch_company_news(symbol: str, date_from: str, date_to: str) -> list[dict]:
    response = requests.get(
        _COMPANY_NEWS_URL,
        params={
            "symbol": symbol,
            "from": date_from,
            "to": date_to,
            "token": get_api_key("FINNHUB_API_KEY"),
        },
    )
    response.raise_for_status()
    return response.json()


def _articles_to_records(symbol: str, articles: list[dict]) -> list[NewsEventRecord]:
    fetched_at = datetime.now(timezone.utc)
    return [
        NewsEventRecord(
            source=SOURCE,
            ts=datetime.fromtimestamp(article["datetime"], tz=timezone.utc),
            symbol=symbol,
            headline=article["headline"],
            raw_payload=article,
            sentiment_score=None,  # non nel payload Finnhub
            url=article["url"],
            fetched_at=fetched_at,
        )
        for article in articles
    ]


def run() -> None:
    symbols = get_universe_symbols()
    logger.info("universo: %d ticker da aggiornare", len(symbols))

    now = datetime.now(timezone.utc)
    date_from = (now - timedelta(hours=_WINDOW_HOURS)).strftime("%Y-%m-%d")
    date_to = now.strftime("%Y-%m-%d")

    with ingestion_run(AUDIT_SOURCE, TARGET_TABLE) as tracker:
        for i, symbol in enumerate(symbols):
            if i > 0:
                time.sleep(random.uniform(*_DELAY_BETWEEN_SYMBOLS))

            try:
                articles = _fetch_company_news(symbol, date_from, date_to)
            except Exception:
                logger.exception("fetch fallito per %s, salto al prossimo ticker", symbol)
                continue

            if not articles:
                continue

            records = _articles_to_records(symbol, articles)

            with get_session() as session:
                try:
                    asset_id = resolve_asset_id(session, symbol)
                except AssetNotFoundError:
                    logger.warning("%s non ancora in t_assets, salto", symbol)
                    continue
                for record in records:
                    write_news_event(session, asset_id, record)
                tracker.rows_written += len(records)

    logger.info("completato: %d righe scritte", tracker.rows_written)


if __name__ == "__main__":
    configure_logging()
    run()
