"""Pipeline `finnhub-news`: company news recenti per l'universo osservato.

Cadenza oraria (timer `marketmind-ingest-finnhub-news.timer`). Per ogni
ticker `GET /company-news` su una finestra di 48h: overlap di sicurezza tra
run consecutivi, l'upsert su `url` deduplica un articolo che ricompare.
Piano free ~60 richieste/min: una piccola pausa tra i ticker basta.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from marketmind_pipelines.base import PerSymbolPipeline
from marketmind_pipelines.http import HttpSource
from marketmind_pipelines.records import NewsEventRecord
from marketmind_pipelines.sinks import NewsEventSink

SOURCE = "Finnhub"  # valore di NewsEventRecord.source (00_schema_interfacce.md)
WINDOW_HOURS = 48


def finnhub_source() -> HttpSource:
    return HttpSource(
        "https://finnhub.io/api/v1", api_key_env="FINNHUB_API_KEY", api_key_param="token"
    )


def articles_to_records(symbol: str, articles: list[dict], fetched_at: datetime) -> list[NewsEventRecord]:
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


class FinnhubNewsPipeline(PerSymbolPipeline[list[dict], NewsEventRecord]):
    name = "finnhub-news"
    audit_source = "finnhub"  # minuscolo: ck_t_ingestion_runs_source
    target_table = "market_data.t_news_events"
    sink = NewsEventSink
    delay_between_targets = (0.2, 0.5)

    def __init__(self, db, *, http: HttpSource | None = None, **kwargs) -> None:
        super().__init__(db, **kwargs)
        self.http = http or finnhub_source()

    def setup(self) -> None:
        now = datetime.now(timezone.utc)
        self.date_from = (now - timedelta(hours=WINDOW_HOURS)).strftime("%Y-%m-%d")
        self.date_to = now.strftime("%Y-%m-%d")

    def extract(self, target: str) -> list[dict]:
        return self.http.get_json(
            "/company-news", params={"symbol": target, "from": self.date_from, "to": self.date_to}
        )

    def transform(self, target: str, raw: list[dict]) -> list[NewsEventRecord]:
        return articles_to_records(target, raw, datetime.now(timezone.utc))
