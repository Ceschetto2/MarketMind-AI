"""Pipeline `yfinance-prices`: prezzi intraday orari per l'universo osservato.

Entry point standalone, invocato dal container Quadlet
`marketmind-ingest-yfinance-prices.container` (`Exec=python -m
marketmind_ai.ingestion.yfinance_prices_pipeline`), a cadenza oraria — vedi
`Market Mind AI - Docs/pipelines/01_trigger_e_scheduling.md`.

Legge la lista di ticker da `market_data.t_universe_members` (non da yfinance
stesso: yfinance non espone un endpoint "lista S&P 500"), scarica l'ultimo
giorno di barre orarie per ciascuno via `yf.Ticker(symbol).history()`, valida
ogni barra come `MarketPriceRecord` e la scrive con upsert idempotente.
Nessun backfill storico profondo (`Market Mind AI - Docs/Data
Providers/01_yfinance_onboarding.md`): `period="1d", interval="60m"` accumula
in avanti, con overlap sufficiente a coprire eventuali run saltati senza
lasciare buchi, senza bisogno di tracciare un "ultimo timestamp visto" per
ticker.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone

import yfinance as yf
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from marketmind_ai.db.session import get_session
from marketmind_ai.db.writer import (
    AssetNotFoundError,
    get_universe_symbols,
    ingestion_run,
    resolve_asset_id,
    upsert_market_price,
)
from marketmind_ai.schemas import MarketPriceRecord
from marketmind_ai.utils.logging_config import configure_logging

logger = logging.getLogger(__name__)

SOURCE = "yfinance"
TARGET_TABLE = "market_data.t_market_prices"

# yfinance non pubblica un limite ufficiale (~360 richieste/ora osservate
# empiricamente, non garantite) — un ritardo casuale tra i ticker e un
# retry con backoff esponenziale sui 429 sono le pratiche raccomandate
# dalla community, non un requisito documentato dalla libreria stessa.
_DELAY_BETWEEN_SYMBOLS = (1.0, 3.0)


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def _fetch_history(symbol: str):
    """`.history()` con retry/backoff esponenziale — non riprovare subito su un 429."""
    return yf.Ticker(symbol).history(period="1d", interval="60m")


def _rows_to_records(symbol: str, history) -> list[MarketPriceRecord]:
    fetched_at = datetime.now(timezone.utc)
    records = []
    for ts, row in history.iterrows():
        records.append(
            MarketPriceRecord(
                symbol=symbol,
                ts=ts.to_pydatetime(),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]),
                source=SOURCE,
                fetched_at=fetched_at,
            )
        )
    return records


def run() -> None:
    symbols = get_universe_symbols()
    logger.info("universo: %d ticker da aggiornare", len(symbols))

    with ingestion_run(SOURCE, TARGET_TABLE) as tracker:
        for i, symbol in enumerate(symbols):
            if i > 0:
                time.sleep(random.uniform(*_DELAY_BETWEEN_SYMBOLS))

            try:
                history = _fetch_history(symbol)
            except Exception:
                logger.exception("fetch fallito per %s, salto al prossimo ticker", symbol)
                continue

            if history.empty:
                logger.warning("nessuna barra restituita per %s", symbol)
                continue

            records = _rows_to_records(symbol, history)

            with get_session() as session:
                try:
                    asset_id = resolve_asset_id(session, symbol)
                except AssetNotFoundError:
                    logger.warning(
                        "%s non ancora in t_assets, salto — esegui yfinance-assets o "
                        "universe-csv prima di questa pipeline",
                        symbol,
                    )
                    continue
                for record in records:
                    upsert_market_price(session, asset_id, record)
                tracker.rows_written += len(records)

    logger.info("completato: %d righe scritte", tracker.rows_written)


if __name__ == "__main__":
    configure_logging()
    run()
