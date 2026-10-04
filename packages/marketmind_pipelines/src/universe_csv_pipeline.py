"""Pipeline `universe-csv`: seed della lista di ticker dell'universo osservato.

Cadenza manuale (`deploy.sh --trigger universe-csv`) più un timer mensile
di sicurezza. L'unica pipeline la cui fonte è un file del repository
(`seeds/universe.csv`, dentro il pacchetto così che viaggi con la wheel
dell'immagine), non un'API esterna, e l'unica che può creare righe in
`t_assets` (`UniverseMemberRecord` è l'unica interfaccia con `asset_type`).

Un CSV malformato fa fallire l'intero run: è un errore da correggere alla
fonte, non un dato da scartare riga per riga.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from marketmind_pipelines.base import BulkPipeline
from marketmind_pipelines.records import UniverseMemberRecord
from marketmind_pipelines.sinks import UniverseMemberSink

SOURCE = "universe-csv"
DEFAULT_CSV_PATH = Path(__file__).resolve().parent / "seeds" / "universe.csv"

_TRUE_VALUES = {"true", "1", "yes"}


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in _TRUE_VALUES


def read_universe_csv(path: Path) -> list[UniverseMemberRecord]:
    """Legge il CSV di seed e valida ogni riga come `UniverseMemberRecord`.

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


class UniverseCsvPipeline(BulkPipeline[list[UniverseMemberRecord], UniverseMemberRecord]):
    name = "universe-csv"
    audit_source = "universe-csv"
    target_table = "market_data.t_universe_members"
    sink = UniverseMemberSink

    def __init__(self, db, *, csv_path: Path = DEFAULT_CSV_PATH) -> None:
        super().__init__(db)
        self.csv_path = csv_path

    def setup(self) -> None:
        # Letto qui, fuori dai target: un CSV malformato o assente non è un
        # target fallito ma un errore di configurazione — il run fallisce e
        # l'eccezione viene rilanciata.
        self.records = read_universe_csv(self.csv_path)

    def fetch(self) -> list[UniverseMemberRecord]:
        return self.records

    def parse(self, raw: list[UniverseMemberRecord]) -> list[UniverseMemberRecord]:
        return raw
