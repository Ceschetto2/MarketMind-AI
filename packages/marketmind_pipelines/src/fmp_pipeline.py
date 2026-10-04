"""Pipeline `fmp`: fondamentali (bilanci, dividendi, split) per gli asset con
earnings recente.

Nessun timer proprio: incatenata a `marketmind-ingest-finnhub-earnings`
via `OnSuccess=` systemd, per restare nel budget di 250 richieste/giorno del
piano free (un giro sull'intero universo × 5 endpoint ne costerebbe
~2.500). Di default interroga solo gli asset con un earnings Finnhub
recente in `t_company_events`, scritti dalla pipeline appena completata.

Un target è la coppia (ticker, endpoint): un endpoint fuori dal piano
(402) o senza dati fallisce da solo, senza perdere gli altri quattro dello
stesso ticker. Ogni endpoint restituisce fino a 5 anni di storico; si
scrive solo il record più recente per `date`, coerente col principio
"nessun backfill storico profondo".
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from marketmind_pipelines.base import PerSymbolPipeline
from marketmind_pipelines.http import HttpSource
from marketmind_pipelines.lookups import symbols_with_recent_company_events
from marketmind_pipelines.records import CompanyEventRecord
from marketmind_pipelines.sinks import CompanyEventSink

SOURCE = "FMP"  # valore di CompanyEventRecord.source (00_schema_interfacce.md)

# endpoint → event_type: tre event_type distinti per i bilanci (migrazione
# `0009`), non tutti `earnings` — collidevano sulla stessa chiave.
ENDPOINTS = {
    "income-statement": "income_statement",
    "balance-sheet-statement": "balance_sheet",
    "cash-flow-statement": "cash_flow",
    "dividends": "dividend",
    "splits": "split",
}

# Finestra per considerare "recente" un earnings Finnhub: copre il ritardo
# tra l'earnings e la pubblicazione dei fondamentali su FMP.
RECENT_EARNINGS_WINDOW_DAYS = 14


def latest_record(statements: list[dict]) -> dict | None:
    if not statements:
        return None
    return max(statements, key=lambda s: s["date"])


def _optional_date(value: str | None) -> date | None:
    """`acceptedDate` porta anche un orario (`YYYY-MM-DD HH:MM:SS`), `filingDate`
    no: i primi 10 caratteri isolano la data in entrambi i casi (il valore
    completo resta in `raw_payload`)."""
    return date.fromisoformat(value[:10]) if value else None


def statement_to_record(
    symbol: str, endpoint: str, statement: dict, fetched_at: datetime
) -> CompanyEventRecord:
    """I sei campi identificativi (migrazione `0008`) esistono solo nei tre
    bilanci, non in dividendi/split: letti con `.get()`."""
    return CompanyEventRecord(
        symbol=symbol,
        ts=date.fromisoformat(statement["date"]),
        event_type=ENDPOINTS[endpoint],
        raw_payload=statement,
        source=SOURCE,
        fetched_at=fetched_at,
        fiscal_year=statement.get("fiscalYear"),
        period=statement.get("period"),
        reported_currency=statement.get("reportedCurrency"),
        cik=statement.get("cik"),
        filing_date=_optional_date(statement.get("filingDate")),
        accepted_date=_optional_date(statement.get("acceptedDate")),
    )


class FmpPipeline(PerSymbolPipeline[list[dict], CompanyEventRecord]):
    name = "fmp"
    audit_source = "fmp"
    target_table = "market_data.t_company_events"
    sink = CompanyEventSink

    def __init__(self, db, *, http: HttpSource | None = None, **kwargs) -> None:
        super().__init__(db, **kwargs)
        # `max_attempts=2`: sul piano free un 429 significa quota giornaliera
        # esaurita, non un limite al minuto — riprovarlo con backoff fa solo
        # perdere tempo; un secondo tentativo copre comunque un errore di rete.
        self.http = http or HttpSource(
            "https://financialmodelingprep.com/stable",
            api_key_env="FMP_API_KEY",
            api_key_param="apikey",
            max_attempts=2,
        )

    def default_symbols(self) -> list[str]:
        since = datetime.now(timezone.utc).date() - timedelta(days=RECENT_EARNINGS_WINDOW_DAYS)
        with self.db.transaction() as tx:
            return symbols_with_recent_company_events(
                tx, source="Finnhub", event_type="earnings", since=since
            )

    def targets(self) -> list[tuple[str, str]]:
        return [(symbol, endpoint) for symbol in self.symbols_to_process() for endpoint in ENDPOINTS]

    def describe_target(self, target: tuple[str, str]) -> str:
        return "/".join(target)

    def extract(self, target: tuple[str, str]) -> list[dict]:
        symbol, endpoint = target
        return self.http.get_json(f"/{endpoint}", params={"symbol": symbol})

    def transform(self, target: tuple[str, str], raw: list[dict]) -> list[CompanyEventRecord]:
        latest = latest_record(raw)
        if latest is None:
            return []
        symbol, endpoint = target
        return [statement_to_record(symbol, endpoint, latest, datetime.now(timezone.utc))]
