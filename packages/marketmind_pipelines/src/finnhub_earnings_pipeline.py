"""Pipeline `finnhub-earnings`: calendario earnings per l'universo osservato.

Cadenza giornaliera (timer `marketmind-ingest-finnhub-earnings.timer`); in
produzione fa anche da trigger della pipeline `fmp` (`OnSuccess=` systemd,
non gestito qui), che legge da `t_company_events` gli asset con earnings
recente scritti da questa pipeline.

`/calendar/earnings` senza `symbol` restituisce gli earnings di tutte le
aziende in una sola chiamata, molto più efficiente che iterare i ticker:
il filtro all'universo avviene lato client dopo il fetch.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from marketmind_pipelines.base import BulkPipeline
from marketmind_pipelines.finnhub_news_pipeline import finnhub_source
from marketmind_pipelines.http import HttpSource
from marketmind_pipelines.lookups import universe_symbols
from marketmind_pipelines.records import CompanyEventRecord
from marketmind_pipelines.sinks import CompanyEventSink

SOURCE = "Finnhub"  # valore di CompanyEventRecord.source (00_schema_interfacce.md)
WINDOW_DAYS_PAST = 7
WINDOW_DAYS_FUTURE = 30


def events_to_records(
    events: list[dict], universe: set[str], fetched_at: datetime
) -> list[CompanyEventRecord]:
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
        if event["symbol"] in universe
    ]


class FinnhubEarningsPipeline(BulkPipeline[list[dict], CompanyEventRecord]):
    name = "finnhub-earnings"
    audit_source = "finnhub"
    target_table = "market_data.t_company_events"
    sink = CompanyEventSink

    def __init__(self, db, *, http: HttpSource | None = None) -> None:
        super().__init__(db)
        self.http = http or finnhub_source()

    def setup(self) -> None:
        with self.db.transaction() as tx:
            self.universe = set(universe_symbols(tx))

    def fetch(self) -> list[dict]:
        today = datetime.now(timezone.utc).date()
        payload = self.http.get_json(
            "/calendar/earnings",
            params={
                "from": (today - timedelta(days=WINDOW_DAYS_PAST)).isoformat(),
                "to": (today + timedelta(days=WINDOW_DAYS_FUTURE)).isoformat(),
            },
        )
        return payload.get("earningsCalendar", [])

    def parse(self, raw: list[dict]) -> list[CompanyEventRecord]:
        return events_to_records(raw, self.universe, datetime.now(timezone.utc))
