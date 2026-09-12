"""Test unitari per la logica pura di `finnhub_earnings_pipeline.py`.

Nessun accesso a rete/DB reale: `requests.get`, `get_api_key`, `get_session`
e lo strato di scrittura sono mockati con `pytest-mock`.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import requests

from marketmind_ai.db.writer import AssetNotFoundError
from marketmind_ai.ingestion.finnhub_earnings_pipeline import (
    _events_to_records,
    _fetch_earnings_calendar,
    run,
)
from marketmind_ai.schemas import CompanyEventRecord


def _make_event(**overrides) -> dict:
    event = {
        "symbol": "AAPL",
        "date": "2026-08-15",
        "hour": "amc",
        "quarter": 3,
        "year": 2026,
        "epsEstimate": 1.5,
        "epsActual": 1.6,
        "revenueEstimate": 90_000_000,
        "revenueActual": 91_000_000,
    }
    event.update(overrides)
    return event


class TestEventsToRecords:
    """Filtra al solo universo — `/calendar/earnings` senza `symbol` restituisce
    earnings di tutte le aziende, non solo quelle dell'universo."""

    def test_maps_fields_for_symbols_in_universe(self):
        events = [_make_event(symbol="AAPL"), _make_event(symbol="MSFT", date="2026-08-16")]

        records = _events_to_records(events, universe_symbols={"AAPL", "MSFT"})

        assert len(records) == 2
        assert all(isinstance(r, CompanyEventRecord) for r in records)

        aapl = next(r for r in records if r.symbol == "AAPL")
        assert aapl.ts == date(2026, 8, 15)
        assert aapl.event_type == "earnings"
        assert aapl.source == "Finnhub"
        assert aapl.raw_payload == _make_event(symbol="AAPL")

    def test_filters_out_symbols_not_in_universe(self):
        events = [_make_event(symbol="AAPL"), _make_event(symbol="NOTINUNIVERSE")]

        records = _events_to_records(events, universe_symbols={"AAPL"})

        assert len(records) == 1
        assert records[0].symbol == "AAPL"

    def test_empty_events_returns_empty_list(self):
        records = _events_to_records([], universe_symbols={"AAPL"})

        assert records == []


class TestFetchEarningsCalendarRetry:
    def test_retries_and_eventually_succeeds(self, mocker):
        expected_events = [_make_event()]
        mock_response = mocker.Mock()
        mock_response.json.return_value = {"earningsCalendar": expected_events}
        mock_response.raise_for_status.return_value = None

        mock_get = mocker.patch(
            "marketmind_ai.ingestion.finnhub_earnings_pipeline.requests.get",
            side_effect=[
                requests.exceptions.ConnectionError("rete non raggiungibile"),
                requests.exceptions.ConnectionError("rete non raggiungibile"),
                mock_response,
            ],
        )
        mocker.patch.object(
            _fetch_earnings_calendar.retry, "sleep", lambda _seconds: None
        )

        result = _fetch_earnings_calendar("fake-key", "2026-08-01", "2026-08-31")

        assert result == expected_events
        assert mock_get.call_count == 3

    def test_propagates_after_stop_after_attempt(self, mocker):
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_earnings_pipeline.requests.get",
            side_effect=requests.exceptions.ConnectionError("rete non raggiungibile"),
        )
        mocker.patch.object(
            _fetch_earnings_calendar.retry, "sleep", lambda _seconds: None
        )

        with pytest.raises(requests.exceptions.ConnectionError):
            _fetch_earnings_calendar("fake-key", "2026-08-01", "2026-08-31")


class TestRun:
    def test_asset_not_found_for_one_symbol_does_not_block_the_others(self, mocker):
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_earnings_pipeline.get_api_key",
            return_value="fake-key",
        )
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_earnings_pipeline.get_universe_symbols",
            return_value=["AAA", "BBB"],
        )
        mocker.patch(
            "marketmind_ai.ingestion.finnhub_earnings_pipeline._fetch_earnings_calendar",
            return_value=[_make_event(symbol="AAA"), _make_event(symbol="BBB")],
        )

        mock_session = mocker.MagicMock(name="session")
        mock_get_session = mocker.patch(
            "marketmind_ai.ingestion.finnhub_earnings_pipeline.get_session"
        )
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_resolve_asset_id = mocker.patch(
            "marketmind_ai.ingestion.finnhub_earnings_pipeline.resolve_asset_id",
            side_effect=[AssetNotFoundError("AAA non trovato in t_assets"), 42],
        )
        mock_write_company_event = mocker.patch(
            "marketmind_ai.ingestion.finnhub_earnings_pipeline.write_company_event"
        )

        tracker = mocker.Mock(rows_written=0)
        mock_ingestion_run = mocker.patch(
            "marketmind_ai.ingestion.finnhub_earnings_pipeline.ingestion_run"
        )
        mock_ingestion_run.return_value.__enter__.return_value = tracker

        run()

        assert mock_resolve_asset_id.call_count == 2
        assert mock_write_company_event.call_count == 1
        args, _ = mock_write_company_event.call_args
        assert args[1] == 42
        assert tracker.rows_written == 1
