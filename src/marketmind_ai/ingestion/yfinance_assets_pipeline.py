"""Pipeline `yfinance-assets`: anagrafica per l'universo osservato.

Entry point standalone, invocato dal container Quadlet
`marketmind-ingest-yfinance-assets.container` — cadenza mensile (già
decisa: l'anagrafica cambia di rado, a differenza dei prezzi orari di
`yfinance-prices`).

`.info` ha ~170 chiavi (`01_yfinance_onboarding.md`); solo `symbol`,
`longName`, `sector`, `quoteType` sono rilevanti per `AssetRecord` — il
resto (dati di mercato realtime, fondamentali riassuntivi, campi anagrafici
estesi) non viene preservato: a differenza di news/eventi societari,
`AssetRecord` non porta un `raw_payload`, non c'è uno schema `raw` per
questa tabella.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone

import yfinance as yf
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from marketmind_ai.db.session import get_session
from marketmind_ai.db.writer import get_universe_symbols, ingestion_run, upsert_asset
from marketmind_ai.schemas import AssetRecord
from marketmind_ai.utils.logging_config import configure_logging

logger = logging.getLogger(__name__)

SOURCE = "yfinance"
TARGET_TABLE = "market_data.t_assets"

_DELAY_BETWEEN_SYMBOLS = (1.0, 3.0)


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _fetch_info(symbol: str) -> dict:
    return yf.Ticker(symbol).info


def _info_to_record(info: dict) -> AssetRecord:
    return AssetRecord(
        symbol=info["symbol"],
        name=info["longName"],
        sector=info.get("sector"),
        asset_type=info["quoteType"].lower(),
        source=SOURCE,
        fetched_at=datetime.now(timezone.utc),
    )


def run() -> None:
    symbols = get_universe_symbols()
    logger.info("universo: %d ticker da aggiornare", len(symbols))

    with ingestion_run(SOURCE, TARGET_TABLE) as tracker:
        for i, symbol in enumerate(symbols):
            if i > 0:
                time.sleep(random.uniform(*_DELAY_BETWEEN_SYMBOLS))

            try:
                info = _fetch_info(symbol)
                record = _info_to_record(info)
            except Exception:
                logger.exception("fetch fallito per %s, salto al prossimo ticker", symbol)
                continue

            with get_session() as session:
                upsert_asset(session, record)
                tracker.rows_written += 1

    logger.info("completato: %d righe scritte", tracker.rows_written)


if __name__ == "__main__":
    configure_logging()
    run()
