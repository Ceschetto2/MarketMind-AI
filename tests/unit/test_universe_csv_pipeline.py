"""Test unitari per la logica pura di `universe_csv_pipeline.py` (parsing CSV).

Nessun accesso a DB: `read_universe_csv` prende un percorso, restituisce
`UniverseMemberRecord` — la scrittura è compito di `db/writer.py`, testata
a parte nei test di integrazione.
"""

from __future__ import annotations

import pytest

from marketmind_ai.ingestion.universe_csv_pipeline import read_universe_csv
from marketmind_ai.schemas import UniverseMemberRecord

CSV_HEADER = "symbol,name,sector,asset_type,is_benchmark\n"


def _write_csv(tmp_path, content: str):
    path = tmp_path / "universe.csv"
    path.write_text(CSV_HEADER + content)
    return path


class TestReadUniverseCsv:
    def test_legge_righe_valide(self, tmp_path):
        path = _write_csv(
            tmp_path,
            "AAPL,Apple Inc.,Technology,equity,false\n"
            "SPY,SPDR S&P 500 ETF Trust,,etf,true\n",
        )

        records = read_universe_csv(path)

        assert len(records) == 2
        assert all(isinstance(r, UniverseMemberRecord) for r in records)

        aapl = next(r for r in records if r.symbol == "AAPL")
        assert aapl.name == "Apple Inc."
        assert aapl.sector == "Technology"
        assert aapl.asset_type == "equity"
        assert aapl.is_benchmark is False
        assert aapl.source == "universe-csv"

        spy = next(r for r in records if r.symbol == "SPY")
        assert spy.is_benchmark is True

    def test_sector_vuoto_diventa_none(self, tmp_path):
        path = _write_csv(tmp_path, "SPY,SPDR S&P 500 ETF Trust,,etf,true\n")

        records = read_universe_csv(path)

        assert records[0].sector is None

    def test_is_benchmark_case_insensitive(self, tmp_path):
        path = _write_csv(
            tmp_path,
            "AAA,A,,equity,TRUE\n"
            "BBB,B,,equity,False\n"
            "CCC,C,,equity,1\n"
            "DDD,D,,equity,0\n",
        )

        records = {r.symbol: r.is_benchmark for r in read_universe_csv(path)}

        assert records == {"AAA": True, "BBB": False, "CCC": True, "DDD": False}

    def test_riga_con_campo_obbligatorio_mancante_solleva(self, tmp_path):
        # asset_type vuoto: campo obbligatorio per UniverseMemberRecord.
        path = _write_csv(tmp_path, "AAA,A,,,false\n")

        with pytest.raises(ValueError):
            read_universe_csv(path)

    def test_file_inesistente_solleva(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_universe_csv(tmp_path / "non_esiste.csv")
