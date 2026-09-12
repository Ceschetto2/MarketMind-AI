"""Test unitari per la logica pura di `fmp_pipeline.py`.

Nessun accesso a rete/DB reale: `requests.get`, `get_api_key`, `get_session`
e lo strato di scrittura sono mockati con `pytest-mock`.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import requests

from marketmind_ai.ingestion.fmp_pipeline import (
    ENDPOINTS,
    SOURCE,
    _fetch_endpoint,
    _latest_record,
    _statements_to_record,
)
from marketmind_ai.schemas import CompanyEventRecord


def _make_statement(**overrides) -> dict:
    statement = {
        "date": "2026-06-30",
        "symbol": "AAPL",
        "reportedCurrency": "USD",
        "cik": "0000320193",
        "fiscalYear": "2026",
        "period": "Q3",
        "filingDate": "2026-07-20",
        "acceptedDate": "2026-07-20",
        "revenue": 90_000_000_000,
    }
    statement.update(overrides)
    return statement


def _make_dividend_or_split(**overrides) -> dict:
    """`dividends`/`splits` non hanno i campi identificativi dei bilanci
    (nessun `fiscalYear`/`cik`/...) — payload realistico per verificare che
    `_statements_to_record` non fallisca sulla loro assenza."""
    payload = {"date": "2026-09-14", "symbol": "AAPL"}
    payload.update(overrides)
    return payload


class TestLatestRecord:
    """Nessun backfill storico profondo (principio generale del progetto):
    di ogni endpoint (che restituisce fino a 5 anni in una sola chiamata) si
    scrive solo il record più recente per `date`, non l'intero storico."""

    def test_returns_most_recent_by_date(self):
        statements = [
            _make_statement(date="2025-06-30"),
            _make_statement(date="2026-06-30"),
            _make_statement(date="2024-06-30"),
        ]

        latest = _latest_record(statements)

        assert latest["date"] == "2026-06-30"

    def test_empty_list_returns_none(self):
        assert _latest_record([]) is None


class TestStatementsToRecord:
    @pytest.mark.parametrize(
        ("endpoint_key", "expected_event_type"),
        [
            # income/balance/cash-flow-statement hanno event_type distinti,
            # non tutti "earnings" — altrimenti collidono sulla stessa
            # chiave se riferiti alla stessa data (agenda #54).
            ("income-statement", "income_statement"),
            ("balance-sheet-statement", "balance_sheet"),
            ("cash-flow-statement", "cash_flow"),
            ("dividends", "dividend"),
            ("splits", "split"),
        ],
    )
    def test_maps_endpoint_to_event_type(self, endpoint_key, expected_event_type):
        statement = _make_statement()

        record = _statements_to_record("AAPL", endpoint_key, statement)

        assert isinstance(record, CompanyEventRecord)
        assert record.symbol == "AAPL"
        assert record.ts == date(2026, 6, 30)
        assert record.event_type == expected_event_type
        assert record.source == SOURCE

    def test_extracts_identifying_fields_for_financial_statements(self):
        """`fiscal_year`/`period`/`reported_currency`/`cik`/`filing_date`/
        `accepted_date` — comuni ai tre bilanci, non ai dividendi/split."""
        statement = _make_statement()

        record = _statements_to_record("AAPL", "income-statement", statement)

        assert record.fiscal_year == "2026"
        assert record.period == "Q3"
        assert record.reported_currency == "USD"
        assert record.cik == "0000320193"
        assert record.filing_date == date(2026, 7, 20)
        assert record.accepted_date == date(2026, 7, 20)

    def test_identifying_fields_none_for_dividends_and_splits(self):
        """Payload realistico senza quei campi: `_statements_to_record` non
        deve sollevare (uso di `.get()`, non accesso diretto a chiave)."""
        record = _statements_to_record(
            "AAPL", "dividends", _make_dividend_or_split(dividend=0.25)
        )

        assert record.fiscal_year is None
        assert record.period is None
        assert record.reported_currency is None
        assert record.cik is None
        assert record.filing_date is None
        assert record.accepted_date is None

    def test_accepted_date_with_time_component_is_truncated_to_date(self):
        """`acceptedDate` reale da FMP porta un timestamp
        (`"2025-10-31 06:01:26"`), non solo la data — la componente oraria
        non viene preservata in questa colonna di comodo (documentato)."""
        statement = _make_statement(acceptedDate="2025-10-31 06:01:26")

        record = _statements_to_record("AAPL", "income-statement", statement)

        assert record.accepted_date == date(2025, 10, 31)


class TestFetchEndpointRetry:
    def test_retries_and_eventually_succeeds(self, mocker):
        expected = [_make_statement()]
        mock_response = mocker.Mock()
        mock_response.json.return_value = expected
        mock_response.raise_for_status.return_value = None

        mock_get = mocker.patch(
            "marketmind_ai.ingestion.fmp_pipeline.requests.get",
            side_effect=[
                requests.exceptions.ConnectionError("rete non raggiungibile"),
                requests.exceptions.ConnectionError("rete non raggiungibile"),
                mock_response,
            ],
        )
        mocker.patch.object(_fetch_endpoint.retry, "sleep", lambda _seconds: None)

        result = _fetch_endpoint("income-statement", "AAPL", "fake-key")

        assert result == expected
        assert mock_get.call_count == 3

    def test_propagates_after_stop_after_attempt(self, mocker):
        mocker.patch(
            "marketmind_ai.ingestion.fmp_pipeline.requests.get",
            side_effect=requests.exceptions.ConnectionError("rete non raggiungibile"),
        )
        mocker.patch.object(_fetch_endpoint.retry, "sleep", lambda _seconds: None)

        with pytest.raises(requests.exceptions.ConnectionError):
            _fetch_endpoint("income-statement", "AAPL", "fake-key")


class TestEndpointsConstant:
    def test_five_endpoints_mapped(self):
        assert set(ENDPOINTS) == {
            "income-statement",
            "balance-sheet-statement",
            "cash-flow-statement",
            "dividends",
            "splits",
        }
