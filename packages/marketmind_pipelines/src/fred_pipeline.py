"""Pipeline `fred`: osservazioni macro vintage (via ALFRED) per un set fisso
di indicatori.

Cadenza giornaliera (timer `marketmind-ingest-fred.timer`): un poll
uniforme cattura qualunque release, qualunque sia il calendario proprio di
ciascun indicatore. Un target per indicatore.

**ALFRED, non le serie FRED standard**: `realtime_start`/`realtime_end`
impostati a *oggi* (momento della query), mai lasciati di default — è il
punto non negoziabile dell'onboarding (`04_fred_onboarding.md`), necessario
per il principio no-look-ahead: un valore rivisto in seguito non deve mai
comparire come se fosse stato noto prima della revisione.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from marketmind_pipelines.base import BasePipeline
from marketmind_pipelines.http import HttpSource
from marketmind_pipelines.records import MacroEventRecord
from marketmind_pipelines.sinks import MacroEventSink

SOURCE = "FRED"  # valore di MacroEventRecord.source (00_schema_interfacce.md)
FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
INDICATORS = ["UNRATE", "CPIAUCSL", "GDP", "FEDFUNDS"]
OBSERVATION_LOOKBACK_DAYS = 60


def parse_observations(indicator: str, payload: dict, fetched_at: datetime) -> list[MacroEventRecord]:
    """`"."` nella risposta = valore non ancora pubblicato → `None`."""
    return [
        MacroEventRecord(
            indicator=indicator,
            ts=date.fromisoformat(obs["date"]),
            value=None if obs["value"] == "." else float(obs["value"]),
            source=SOURCE,
            fetched_at=fetched_at,
        )
        for obs in payload.get("observations", [])
    ]


class FredPipeline(BasePipeline[str, dict, MacroEventRecord]):
    name = "fred"
    audit_source = "fred"
    target_table = "market_data.t_macro_events"
    sink = MacroEventSink

    def __init__(
        self, db, *, indicators: list[str] | None = None, http: HttpSource | None = None
    ) -> None:
        super().__init__(db)
        self.indicators = indicators or INDICATORS
        self.http = http or HttpSource(api_key_env="FRED_API_KEY", api_key_param="api_key")

    def setup(self) -> None:
        self.today = datetime.now(timezone.utc).date()

    def targets(self) -> list[str]:
        return self.indicators

    def extract(self, target: str) -> dict:
        return self.http.get_json(
            FRED_OBSERVATIONS_URL,
            params={
                "series_id": target,
                "file_type": "json",
                "realtime_start": self.today.isoformat(),
                "realtime_end": self.today.isoformat(),
                "observation_start": (self.today - timedelta(days=OBSERVATION_LOOKBACK_DAYS)).isoformat(),
            },
        )

    def transform(self, target: str, raw: dict) -> list[MacroEventRecord]:
        return parse_observations(target, raw, datetime.now(timezone.utc))
