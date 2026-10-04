"""Test unitari di `fmp_pipeline.py`: `HttpSource` è un mock."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from marketmind_pipelines.fmp_pipeline import (
    ENDPOINTS,
    SOURCE,
    FmpPipeline,
    latest_record,
    statement_to_record,
)
from marketmind_pipelines.http import HttpSource
from marketmind_pipelines.records import CompanyEventRecord

_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _statement(**overrides) -> dict:
    statement = {
        "date": "2026-06-30",
        "symbol": "AAPL",
        "reportedCurrency": "USD",
        "cik": "0000320193",
        "filingDate": "2026-08-01",
        "acceptedDate": "2026-08-01 18:04:12",
        "fiscalYear": "2026",
        "period": "Q3",
        "revenue": 90_000_000_000,
    }
    statement.update(overrides)
    return statement


class TestLatestRecord:
    """Nessun backfill storico profondo: di ogni endpoint (fino a 5 anni per
    chiamata) si scrive solo il record più recente per `date`."""

    def test_il_piu_recente_per_data(self):
        statements = [_statement(date="2025-06-30"), _statement(date="2026-06-30"), _statement(date="2024-06-30")]

        assert latest_record(statements)["date"] == "2026-06-30"

    def test_lista_vuota(self):
        assert latest_record([]) is None


class TestStatementToRecord:
    @pytest.mark.parametrize(
        ("endpoint", "event_type"),
        [
            ("income-statement", "income_statement"),
            ("balance-sheet-statement", "balance_sheet"),
            ("cash-flow-statement", "cash_flow"),
            ("dividends", "dividend"),
            ("splits", "split"),
        ],
    )
    def test_endpoint_mappato_sul_proprio_event_type(self, endpoint, event_type):
        """Migrazione `0009`: i tre bilanci non sono più tutti `earnings`."""
        statement = _statement()

        record = statement_to_record("AAPL", endpoint, statement, _NOW)

        assert isinstance(record, CompanyEventRecord)
        assert (record.symbol, record.ts, record.event_type) == ("AAPL", date(2026, 6, 30), event_type)
        assert record.source == SOURCE
        assert record.raw_payload == statement

    def test_colonne_identificative_dei_bilanci(self):
        """Migrazione `0008`; `acceptedDate` porta anche l'orario."""
        record = statement_to_record("AAPL", "income-statement", _statement(), _NOW)

        assert (record.fiscal_year, record.period, record.reported_currency, record.cik) == (
            "2026", "Q3", "USD", "0000320193",
        )
        assert (record.filing_date, record.accepted_date) == (date(2026, 8, 1), date(2026, 8, 1))

    def test_dividendo_senza_colonne_identificative(self):
        record = statement_to_record("AAPL", "dividends", {"date": "2026-08-10", "dividend": 0.26}, _NOW)

        assert record.fiscal_year is None and record.filing_date is None


class TestPipeline:
    def test_cinque_endpoint(self):
        assert len(ENDPOINTS) == 5

    def test_un_target_per_coppia_ticker_endpoint(self):
        """Un endpoint fuori piano (402) fallisce da solo, senza perdere gli
        altri quattro dello stesso ticker."""
        targets = FmpPipeline(db=None, http=None, symbols=["AAPL", "MSFT"]).targets()

        assert len(targets) == 10
        assert targets[:2] == [("AAPL", "income-statement"), ("AAPL", "balance-sheet-statement")]

    def test_extract_chiama_lendpoint_del_target(self, mocker):
        http = mocker.MagicMock(spec=HttpSource)
        http.get_json.return_value = []

        FmpPipeline(db=None, http=http, symbols=["AAPL"]).extract(("AAPL", "dividends"))

        http.get_json.assert_called_once_with("/dividends", params={"symbol": "AAPL"})

    def test_transform_solo_il_record_piu_recente(self):
        pipeline = FmpPipeline(db=None, http=None, symbols=["AAPL"])

        records = pipeline.transform(
            ("AAPL", "income-statement"), [_statement(date="2025-06-30"), _statement(date="2026-06-30")]
        )

        assert [r.ts for r in records] == [date(2026, 6, 30)]

    def test_transform_endpoint_vuoto(self):
        assert FmpPipeline(db=None, http=None).transform(("AAPL", "splits"), []) == []

    def test_descrizione_del_target(self):
        assert FmpPipeline(db=None, http=None).describe_target(("AAPL", "splits")) == "AAPL/splits"
