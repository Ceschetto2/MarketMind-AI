"""Pipeline `fred`: osservazioni macro (vintage, via ALFRED) per un set fisso
di indicatori.

Entry point standalone, invocato dal container Quadlet
`marketmind-ingest-fred.container` — cadenza giornaliera (già decisa, vedi
`Market Mind AI - Docs/pipelines/01_trigger_e_scheduling.md`): un poll
uniforme cattura qualunque release, indipendentemente dal calendario di
rilascio proprio di ciascun indicatore.

**ALFRED, non le serie FRED standard**: `realtime_start`/`realtime_end`
impostati a *oggi* (momento della query), mai lasciati di default — è il
punto non negoziabile dell'onboarding (`04_fred_onboarding.md`), necessario
per il principio no-look-ahead del progetto: un valore rivisto in seguito
(es. il PIL trimestrale) non deve mai comparire come se fosse stato noto
prima della revisione.

`FRED_OBSERVATIONS_URL`, `INDICATORS` e `SOURCE` sono costanti a livello di
modulo (non solo dettagli implementativi): i test le importano direttamente
per non duplicare le stringhe magiche.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from marketmind_ai.config import get_api_key
from marketmind_ai.db.session import get_session
from marketmind_ai.db.writer import ingestion_run, upsert_macro_event
from marketmind_ai.schemas import MacroEventRecord
from marketmind_ai.utils.logging_config import configure_logging

logger = logging.getLogger(__name__)

SOURCE = "FRED"  # valore di MacroEventRecord.source (00_schema_interfacce.md)
AUDIT_SOURCE = "fred"  # valore ammesso da ck_t_ingestion_runs_source (minuscolo)
TARGET_TABLE = "market_data.t_macro_events"

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"

# Lista iniziale, non esaustiva: i quattro esempi già citati nel documento
# di onboarding (inflazione, disoccupazione, PIL, tassi) — coprono le
# quattro famiglie di indicatori macro più rilevanti per il context package
# senza appesantire inutilmente ogni run (4 chiamate, ben dentro i 120/min).
INDICATORS = ["UNRATE", "CPIAUCSL", "GDP", "FEDFUNDS"]

# Nessun backfill storico profondo (principio generale del progetto): una
# finestra di due mesi indietro basta a non perdere un'osservazione recente
# se un run viene saltato, senza riscaricare anni di storico ogni giorno.
_OBSERVATION_LOOKBACK_DAYS = 60


def _today() -> date:
    return datetime.now(timezone.utc).date()


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _fetch_observations(
    series_id: str, api_key: str, today: date, observation_start: date
) -> dict:
    response = requests.get(
        FRED_OBSERVATIONS_URL,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "realtime_start": today.isoformat(),
            "realtime_end": today.isoformat(),
            "observation_start": observation_start.isoformat(),
        },
    )
    response.raise_for_status()
    return response.json()


def _parse_observations(
    indicator: str, payload: dict, fetched_at: datetime
) -> list[MacroEventRecord]:
    records = []
    for obs in payload.get("observations", []):
        raw_value = obs["value"]
        # "." significa "non ancora pubblicato" — mai convertito ciecamente
        # a float, la vera insidia segnalata in 04_fred_onboarding.md.
        value = None if raw_value == "." else float(raw_value)
        records.append(
            MacroEventRecord(
                indicator=indicator,
                ts=date.fromisoformat(obs["date"]),
                value=value,
                source=SOURCE,
                fetched_at=fetched_at,
            )
        )
    return records


def run() -> None:
    api_key = get_api_key("FRED_API_KEY")
    today = _today()
    observation_start = today - timedelta(days=_OBSERVATION_LOOKBACK_DAYS)
    fetched_at = datetime.now(timezone.utc)

    with ingestion_run(AUDIT_SOURCE, TARGET_TABLE) as tracker:
        with get_session() as session:
            for indicator in INDICATORS:
                try:
                    payload = _fetch_observations(indicator, api_key, today, observation_start)
                except Exception:
                    logger.exception(
                        "fetch fallito per %s, salto al prossimo indicatore", indicator
                    )
                    continue

                records = _parse_observations(indicator, payload, fetched_at)
                for record in records:
                    upsert_macro_event(session, record)
                    tracker.rows_written += 1

    logger.info("completato: %d righe scritte", tracker.rows_written)


if __name__ == "__main__":
    configure_logging()
    run()
