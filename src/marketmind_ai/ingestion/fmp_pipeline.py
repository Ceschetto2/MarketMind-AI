"""Pipeline `fmp`: fondamentali (bilanci, dividendi, split) per gli asset con
earnings recente.

Entry point standalone, invocato dal container Quadlet
`marketmind-ingest-fmp.container` — **nessun timer proprio**: incatenata a
`marketmind-ingest-finnhub-earnings.service` via `OnSuccess=` systemd (vedi
quel `.container`), per restare dentro il budget di 250 richieste/giorno
del piano free contro le ~2.500 di un giro sull'intero universo × 5
endpoint. Non itera `get_universe_symbols()`: legge da
`market_data.t_company_events` gli asset con un earnings Finnhub recente —
esattamente quelli scritti dalla pipeline appena completata.

Ogni endpoint restituisce fino a 5 anni di storico in una sola chiamata;
coerente col principio "nessun backfill storico profondo" del progetto, si
scrive solo il record più recente per `date`, non l'intero storico.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import requests
from sqlalchemy import select
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from marketmind_ai.config import get_api_key
from marketmind_ai.db.models.market_data import Asset, CompanyEvent
from marketmind_ai.db.session import get_session
from marketmind_ai.db.writer import AssetNotFoundError, ingestion_run, resolve_asset_id, write_company_event
from marketmind_ai.schemas import CompanyEventRecord
from marketmind_ai.utils.logging_config import configure_logging

logger = logging.getLogger(__name__)

SOURCE = "FMP"  # valore di CompanyEventRecord.source (00_schema_interfacce.md)
AUDIT_SOURCE = "fmp"  # valore ammesso da ck_t_ingestion_runs_source (minuscolo)
TARGET_TABLE = "market_data.t_company_events"

_BASE_URL = "https://financialmodelingprep.com/stable"

# endpoint -> event_type (00_schema_interfacce.md, CompanyEventRecord.event_type)
ENDPOINTS = {
    "income-statement": "earnings",
    "balance-sheet-statement": "earnings",
    "cash-flow-statement": "earnings",
    "dividends": "dividend",
    "splits": "split",
}

# Finestra su t_company_events (source='Finnhub', event_type='earnings') per
# considerare un earnings "recente": abbastanza ampia da coprire il ritardo
# tra l'earnings di Finnhub e la pubblicazione dei fondamentali su FMP.
_RECENT_EARNINGS_WINDOW_DAYS = 14


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _fetch_endpoint(endpoint: str, symbol: str, api_key: str) -> list[dict]:
    response = requests.get(
        f"{_BASE_URL}/{endpoint}", params={"symbol": symbol, "apikey": api_key}
    )
    response.raise_for_status()
    return response.json()


def _latest_record(statements: list[dict]) -> dict | None:
    if not statements:
        return None
    return max(statements, key=lambda s: s["date"])


def _parse_optional_date(value: str | None) -> date | None:
    """`filingDate` è solo data (`YYYY-MM-DD`), ma `acceptedDate` osservato
    su dati reali porta anche un orario (`YYYY-MM-DD HH:MM:SS`) — i primi 10
    caratteri isolano la data in entrambi i casi, la componente oraria non
    viene preservata in questa colonna di comodo (il valore completo resta
    comunque in `raw_payload`).
    """
    return date.fromisoformat(value[:10]) if value else None


def _statements_to_record(symbol: str, endpoint: str, statement: dict) -> CompanyEventRecord:
    """`fiscal_year`/`period`/`reported_currency`/`cik`/`filing_date`/
    `accepted_date` sono comuni ai tre bilanci (income/balance-sheet/
    cash-flow-statement), assenti dai payload di `dividends`/`splits` —
    letti con `.get()`, mai un accesso diretto a chiave, per non sollevare
    su un endpoint che non li ha.
    """
    return CompanyEventRecord(
        symbol=symbol,
        ts=date.fromisoformat(statement["date"]),
        event_type=ENDPOINTS[endpoint],
        raw_payload=statement,
        source=SOURCE,
        fetched_at=datetime.now(timezone.utc),
        fiscal_year=statement.get("fiscalYear"),
        period=statement.get("period"),
        reported_currency=statement.get("reportedCurrency"),
        cik=statement.get("cik"),
        filing_date=_parse_optional_date(statement.get("filingDate")),
        accepted_date=_parse_optional_date(statement.get("acceptedDate")),
    )


def _symbols_with_recent_earnings(session) -> list[str]:
    """Asset con earnings Finnhub recente in `t_company_events` — il
    sottoinsieme che rende questa pipeline sostenibile nel budget FMP,
    invece dell'intero universo.
    """
    since = datetime.now(timezone.utc).date() - timedelta(days=_RECENT_EARNINGS_WINDOW_DAYS)
    return list(
        session.execute(
            select(Asset.symbol)
            .join(CompanyEvent, CompanyEvent.asset_id == Asset.asset_id)
            .where(
                CompanyEvent.source == "Finnhub",
                CompanyEvent.event_type == "earnings",
                CompanyEvent.ts >= since,
            )
            .distinct()
        ).scalars()
    )


def run() -> None:
    api_key = get_api_key("FMP_API_KEY")

    with get_session() as session:
        symbols = _symbols_with_recent_earnings(session)
    logger.info("%d asset con earnings recente da interrogare su FMP", len(symbols))

    with ingestion_run(AUDIT_SOURCE, TARGET_TABLE) as tracker:
        for symbol in symbols:
            with get_session() as session:
                try:
                    asset_id = resolve_asset_id(session, symbol)
                except AssetNotFoundError:
                    logger.warning("%s non ancora in t_assets, salto", symbol)
                    continue

                for endpoint in ENDPOINTS:
                    try:
                        statements = _fetch_endpoint(endpoint, symbol, api_key)
                    except Exception:
                        logger.exception(
                            "fetch fallito per %s/%s, salto questo endpoint", symbol, endpoint
                        )
                        continue

                    latest = _latest_record(statements)
                    if latest is None:
                        continue

                    record = _statements_to_record(symbol, endpoint, latest)
                    write_company_event(session, asset_id, record)
                    tracker.rows_written += 1

    logger.info("completato: %d righe scritte", tracker.rows_written)


if __name__ == "__main__":
    configure_logging()
    run()
