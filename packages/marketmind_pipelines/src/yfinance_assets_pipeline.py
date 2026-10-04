"""Pipeline `yfinance-assets`: anagrafica per l'universo osservato.

Cadenza mensile (timer `marketmind-ingest-yfinance-assets.timer`):
l'anagrafica cambia di rado, a differenza dei prezzi orari.

`.info` ha ~170 chiavi (`01_yfinance_onboarding.md`); solo `symbol`,
`longName`, `sector`, `quoteType` servono ad `AssetRecord` — il resto non
viene preservato: `AssetRecord` non porta un `raw_payload`, non c'è uno
schema `raw` per questa tabella.
"""

from __future__ import annotations

from datetime import datetime, timezone

import yfinance as yf

from marketmind_pipelines.base import PerSymbolPipeline
from marketmind_pipelines.http import call_with_retry
from marketmind_pipelines.records import AssetRecord
from marketmind_pipelines.sinks import AssetSink

SOURCE = "yfinance"


def info_to_record(info: dict, fetched_at: datetime) -> AssetRecord:
    return AssetRecord(
        symbol=info["symbol"],
        name=info["longName"],
        sector=info.get("sector"),
        asset_type=info["quoteType"].lower(),
        source=SOURCE,
        fetched_at=fetched_at,
    )


class YFinanceAssetsPipeline(PerSymbolPipeline[dict, AssetRecord]):
    name = "yfinance-assets"
    audit_source = "yfinance"
    target_table = "market_data.t_assets"
    sink = AssetSink
    delay_between_targets = (1.0, 3.0)

    def extract(self, target: str) -> dict:
        return call_with_retry(lambda: yf.Ticker(target).info, sleep=self._sleep)

    def transform(self, target: str, raw: dict) -> list[AssetRecord]:
        return [info_to_record(raw, datetime.now(timezone.utc))]
