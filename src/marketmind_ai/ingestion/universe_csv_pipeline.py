"""Pipeline `universe-csv`: seed della lista di ticker dell'universo osservato.

Entry point standalone, invocato dal container Quadlet
`marketmind-ingest-universe-csv.container` (`Exec=python -m
marketmind_ai.ingestion.universe_csv_pipeline`) — a differenza delle altre
pipeline, a cadenza manuale (`deploy.sh --trigger universe-csv`) più un
timer mensile di sicurezza, non oraria/giornaliera: vedi `Market Mind AI -
Docs/pipelines/01_trigger_e_scheduling.md`.

L'unica pipeline la cui fonte è un file nel repository (`seeds/universe.csv`),
non un'API esterna: legge il CSV, valida ogni riga come
`UniverseMemberRecord` e la scrive con `resolve_or_create_asset` (crea la
riga in `t_assets` se manca — l'unica pipeline che può farlo, dato che è
l'unica interfaccia con `asset_type`) + `upsert_universe_member`.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

from marketmind_ai.db.session import get_session
from marketmind_ai.db.writer import ingestion_run, resolve_or_create_asset, upsert_universe_member
from marketmind_ai.schemas import UniverseMemberRecord
from marketmind_ai.utils.logging_config import configure_logging

logger = logging.getLogger(__name__)

SOURCE = "universe-csv"
TARGET_TABLE = "market_data.t_universe_members"

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CSV_PATH = _REPO_ROOT / "seeds" / "universe.csv"

_TRUE_VALUES = {"true", "1", "yes"}


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in _TRUE_VALUES


def read_universe_csv(path: Path) -> list[UniverseMemberRecord]:
    """Legge `seeds/universe.csv` e valida ogni riga come `UniverseMemberRecord`.

    Colonne attese: `symbol,name,sector,asset_type,is_benchmark`. `sector`
    vuoto diventa `None` (non tutte le righe hanno un settore GICS noto);
    `is_benchmark` accetta `true`/`false`, `1`/`0`, `yes`/`no`, case-insensitive.
    Una riga con un campo obbligatorio mancante (`ValidationError` di
    Pydantic) fa fallire l'intera lettura — un CSV di seed malformato è un
    errore da correggere alla fonte, non un dato da scartare riga per riga.
    """
    if not path.exists():
        raise FileNotFoundError(f"CSV di seed non trovato: {path}")

    fetched_at = datetime.now(timezone.utc)
    records = []
    with path.open(newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):  # riga 1 = header
            symbol, name, asset_type = (
                row["symbol"].strip(),
                row["name"].strip(),
                row["asset_type"].strip(),
            )
            # `str` di Pydantic accetta una stringa vuota (non è "mancante"
            # per il validatore) — un CSV con una colonna obbligatoria
            # lasciata vuota va comunque respinto esplicitamente qui.
            if not symbol or not name or not asset_type:
                raise ValueError(
                    f"riga {i} di {path}: symbol/name/asset_type non possono essere vuoti"
                )
            records.append(
                UniverseMemberRecord(
                    symbol=symbol,
                    name=name,
                    sector=row["sector"].strip() or None,
                    asset_type=asset_type,
                    is_benchmark=_parse_bool(row["is_benchmark"]),
                    source=SOURCE,
                    fetched_at=fetched_at,
                )
            )
    return records


def run(csv_path: Path = DEFAULT_CSV_PATH) -> None:
    records = read_universe_csv(csv_path)
    logger.info("letti %d ticker da %s", len(records), csv_path)

    with ingestion_run(SOURCE, TARGET_TABLE) as tracker:
        with get_session() as session:
            for record in records:
                asset_id = resolve_or_create_asset(session, record)
                upsert_universe_member(session, asset_id, record)
                tracker.rows_written += 1

    logger.info("completato: %d righe scritte", tracker.rows_written)


if __name__ == "__main__":
    configure_logging()
    run()
