"""Pipeline `finnhub-earnings`: calendario earnings per l'universo osservato.

Entry point standalone, invocato dal container Quadlet
`marketmind-ingest-finnhub-earnings.container` — cadenza giornaliera (già
decisa, il calendario cambia poco da un giorno all'altro). In produzione fa
anche da trigger per la pipeline `fmp` (incatenata via `OnSuccess=` systemd
sul `.service`, non gestito qui), che legge da `t_company_events` gli asset
con earnings recente scritti da questa pipeline.

`/calendar/earnings` **senza** `symbol` restituisce già gli earnings di
tutte le aziende in una sola chiamata — molto più efficiente che iterare
sui ~26 ticker uno per uno (a differenza di `/company-news`, che richiede
`symbol`). Il filtro all'universo avviene lato client dopo il fetch.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from marketmind_ai.config import get_api_key
from marketmind_ai.db.session import get_session
from marketmind_ai.db.writer import (
    AssetNotFoundError,
    get_universe_symbols,
    ingestion_run,
    resolve_asset_id,
    write_company_event,
)
from marketmind_ai.schemas import CompanyEventRecord
from marketmind_ai.utils.logging_config import configure_logging

logger = logging.getLogger(__name__)

SOURCE = "Finnhub"  # valore di CompanyEventRecord.source (00_schema_interfacce.md)
AUDIT_SOURCE = "finnhub"  # valore ammesso da ck_t_ingestion_runs_source (minuscolo)
TARGET_TABLE = "market_data.t_company_events"

_EARNINGS_CALENDAR_URL = "https://finnhub.io/api/v1/calendar/earnings"
# Finestra ampia a sufficienza da coprire earnings appena passati e
# imminenti, senza backfill storico profondo — coerente col resto del
# progetto (nessuna fonte fa backfill profondo).
_WINDOW_DAYS_PAST = 7
_WINDOW_DAYS_FUTURE = 30


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _fetch_earnings_calendar(api_key: str, date_from: str, date_to: str) -> list[dict]:
    response = requests.get(
        _EARNINGS_CALENDAR_URL,
        params={"from": date_from, "to": date_to, "token": api_key},
    )
    response.raise_for_status()
    return response.json().get("earningsCalendar", [])


def _events_to_records(
    events: list[dict], universe_symbols: set[str]
) -> list[CompanyEventRecord]:
    fetched_at = datetime.now(timezone.utc)
    return [
        CompanyEventRecord(
            symbol=event["symbol"],
            ts=date.fromisoformat(event["date"]),
            event_type="earnings",
            raw_payload=event,
            source=SOURCE,
            fetched_at=fetched_at,
        )
        for event in events
        if event["symbol"] in universe_symbols
    ]


def run() -> None:
    api_key = get_api_key("FINNHUB_API_KEY")
    universe_symbols = set(get_universe_symbols())

    today = datetime.now(timezone.utc).date()
    date_from = (today - timedelta(days=_WINDOW_DAYS_PAST)).isoformat()
    date_to = (today + timedelta(days=_WINDOW_DAYS_FUTURE)).isoformat()

    with ingestion_run(AUDIT_SOURCE, TARGET_TABLE) as tracker:
        events = _fetch_earnings_calendar(api_key, date_from, date_to)
        records = _events_to_records(events, universe_symbols)
        logger.info(
            "%d earnings nella finestra, %d nell'universo", len(events), len(records)
        )

        with get_session() as session:
            for record in records:
                try:
                    asset_id = resolve_asset_id(session, record.symbol)
                except AssetNotFoundError:
                    logger.warning("%s non ancora in t_assets, salto", record.symbol)
                    continue
                write_company_event(session, asset_id, record)
                tracker.rows_written += 1

    logger.info("completato: %d righe scritte", tracker.rows_written)


if __name__ == "__main__":
    configure_logging()
    run()
