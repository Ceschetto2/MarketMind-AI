"""Test unitari per la logica pura di `fred_pipeline.py`.

Nessun accesso a rete/DB reale: `requests.get`, `get_session`, `get_api_key`
e lo strato di scrittura (`db/writer.py`) sono mockati con `pytest-mock`.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from marketmind_ai.ingestion.fred_pipeline import (
    FRED_OBSERVATIONS_URL,
    INDICATORS,
    SOURCE,
    _fetch_observations,
    _parse_observations,
    run,
)
from marketmind_ai.schemas import MacroEventRecord


def _make_payload(observations: list[dict]) -> dict:
    """Forma minima della risposta di `/fred/series/observations`, con solo
    i campi che `_parse_observations` legge davvero."""
    return {"observations": observations}


class TestParseObservations:
    def test_maps_fields(self):
        fetched_at = datetime(2026, 9, 6, tzinfo=timezone.utc)
        payload = _make_payload(
            [
                {
                    "realtime_start": "2026-09-06",
                    "realtime_end": "2026-09-06",
                    "date": "2026-08-01",
                    "value": "3.6",
                }
            ]
        )

        records = _parse_observations("UNRATE", payload, fetched_at)

        assert len(records) == 1
        record = records[0]
        assert isinstance(record, MacroEventRecord)
        assert record.indicator == "UNRATE"
        assert record.ts == date(2026, 8, 1)
        assert record.value == 3.6
        assert type(record.value) is float
        assert record.source == SOURCE
        assert record.fetched_at == fetched_at

    def test_value_dot_maps_to_none(self):
        """`value == "."` significa "non ancora pubblicato" — va mappato a
        `None`, mai convertito ciecamente a float (la vera insidia segnalata
        in `04_fred_onboarding.md`)."""
        fetched_at = datetime(2026, 9, 6, tzinfo=timezone.utc)
        payload = _make_payload(
            [{"date": "2026-08-01", "value": "."}]
        )

        records = _parse_observations("GDP", payload, fetched_at)

        assert len(records) == 1
        assert records[0].value is None

    def test_multiple_observations(self):
        fetched_at = datetime(2026, 9, 6, tzinfo=timezone.utc)
        payload = _make_payload(
            [
                {"date": "2026-07-01", "value": "3.5"},
                {"date": "2026-08-01", "value": "."},
            ]
        )

        records = _parse_observations("UNRATE", payload, fetched_at)

        assert len(records) == 2
        assert records[0].value == 3.5
        assert records[1].value is None

    def test_no_observations_returns_empty_list(self):
        fetched_at = datetime(2026, 9, 6, tzinfo=timezone.utc)

        records = _parse_observations("UNRATE", _make_payload([]), fetched_at)

        assert records == []


class TestFetchObservationsRetry:
    """`_fetch_observations` è decorata con `tenacity.retry` (stesso pattern
    di `_fetch_history` in `yfinance_prices_pipeline.py`: `stop_after_attempt(4)`,
    `wait_exponential` reale). Azzeriamo `sleep` sull'oggetto `Retrying`
    attaccato alla funzione decorata invece di toccare la configurazione in
    produzione — il conteggio dei tentativi resta quello vero.
    """

    def test_sends_realtime_start_end_and_observation_start(self, mocker):
        """`realtime_start`/`realtime_end` vanno impostati a *oggi* (momento
        della query), non lasciati di default — è il punto non negoziabile
        dell'onboarding FRED (ALFRED, no-look-ahead)."""
        mock_response = mocker.Mock()
        mock_response.json.return_value = _make_payload([])
        mock_get = mocker.patch(
            "marketmind_ai.ingestion.fred_pipeline.requests.get",
            return_value=mock_response,
        )

        today = date(2026, 9, 6)
        observation_start = date(2026, 7, 8)

        _fetch_observations("UNRATE", "fake-key", today, observation_start)

        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert args[0] == FRED_OBSERVATIONS_URL
        params = kwargs["params"]
        assert params["series_id"] == "UNRATE"
        assert params["api_key"] == "fake-key"
        assert params["file_type"] == "json"
        assert params["realtime_start"] == "2026-09-06"
        assert params["realtime_end"] == "2026-09-06"
        assert params["observation_start"] == "2026-07-08"
        mock_response.raise_for_status.assert_called_once()

    def test_retries_and_eventually_succeeds(self, mocker):
        expected_payload = _make_payload([{"date": "2026-08-01", "value": "3.6"}])
        expected_response = mocker.Mock()
        expected_response.json.return_value = expected_payload
        mock_get = mocker.patch(
            "marketmind_ai.ingestion.fred_pipeline.requests.get",
            side_effect=[
                ConnectionError("rete non raggiungibile"),
                ConnectionError("rete non raggiungibile"),
                expected_response,
            ],
        )
        mocker.patch.object(_fetch_observations.retry, "sleep", lambda _seconds: None)

        result = _fetch_observations("UNRATE", "fake-key", date(2026, 9, 6), date(2026, 7, 8))

        assert result == expected_payload
        assert mock_get.call_count == 3

    def test_propagates_after_stop_after_attempt(self, mocker):
        mocker.patch(
            "marketmind_ai.ingestion.fred_pipeline.requests.get",
            side_effect=ConnectionError("rete non raggiungibile"),
        )
        mocker.patch.object(_fetch_observations.retry, "sleep", lambda _seconds: None)

        with pytest.raises(ConnectionError):
            _fetch_observations("UNRATE", "fake-key", date(2026, 9, 6), date(2026, 7, 8))

    def test_raises_on_http_error_status(self, mocker):
        """Un 429 (o altro status non-2xx) fa scattare `raise_for_status`,
        intercettato dallo stesso retry — non un percorso separato."""
        import requests

        mock_response = mocker.Mock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "429 Too Many Requests"
        )
        mocker.patch(
            "marketmind_ai.ingestion.fred_pipeline.requests.get",
            return_value=mock_response,
        )
        mocker.patch.object(_fetch_observations.retry, "sleep", lambda _seconds: None)

        with pytest.raises(requests.exceptions.HTTPError):
            _fetch_observations("UNRATE", "fake-key", date(2026, 9, 6), date(2026, 7, 8))


class TestRun:
    """Flusso di alto livello di `run()`, con tutte le dipendenze esterne
    (chiave API, fetch, sessione DB, strato di scrittura, audit) mockate."""

    def test_iterates_all_indicators_and_writes_records(self, mocker):
        mocker.patch(
            "marketmind_ai.ingestion.fred_pipeline.get_api_key", return_value="fake-key"
        )

        fake_records = {
            indicator: [mocker.Mock(name=f"record_{indicator}")]
            for indicator in INDICATORS
        }
        mocker.patch(
            "marketmind_ai.ingestion.fred_pipeline._fetch_observations",
            side_effect=lambda series_id, *a, **kw: f"payload_{series_id}",
        )
        mocker.patch(
            "marketmind_ai.ingestion.fred_pipeline._parse_observations",
            side_effect=lambda indicator, payload, fetched_at: fake_records[indicator],
        )

        mock_session = mocker.MagicMock(name="session")
        mock_get_session = mocker.patch("marketmind_ai.ingestion.fred_pipeline.get_session")
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_upsert = mocker.patch("marketmind_ai.ingestion.fred_pipeline.upsert_macro_event")

        tracker = mocker.Mock(rows_written=0)
        mock_ingestion_run = mocker.patch("marketmind_ai.ingestion.fred_pipeline.ingestion_run")
        mock_ingestion_run.return_value.__enter__.return_value = tracker

        run()

        assert mock_upsert.call_count == len(INDICATORS)
        for indicator in INDICATORS:
            mock_upsert.assert_any_call(mock_session, fake_records[indicator][0])
        assert tracker.rows_written == len(INDICATORS)

    def test_fetch_failure_for_one_indicator_does_not_block_the_others(self, mocker):
        mocker.patch(
            "marketmind_ai.ingestion.fred_pipeline.get_api_key", return_value="fake-key"
        )

        mocker.patch(
            "marketmind_ai.ingestion.fred_pipeline._fetch_observations",
            side_effect=[Exception("fetch fallito"), "payload_ok", "payload_ok", "payload_ok"],
        )
        good_record = mocker.Mock(name="record_ok")
        mocker.patch(
            "marketmind_ai.ingestion.fred_pipeline._parse_observations",
            return_value=[good_record],
        )

        mock_session = mocker.MagicMock(name="session")
        mock_get_session = mocker.patch("marketmind_ai.ingestion.fred_pipeline.get_session")
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_upsert = mocker.patch("marketmind_ai.ingestion.fred_pipeline.upsert_macro_event")

        tracker = mocker.Mock(rows_written=0)
        mock_ingestion_run = mocker.patch("marketmind_ai.ingestion.fred_pipeline.ingestion_run")
        mock_ingestion_run.return_value.__enter__.return_value = tracker

        run()

        # Il primo indicatore fallisce e viene saltato, gli altri tre no.
        assert mock_upsert.call_count == len(INDICATORS) - 1
        assert tracker.rows_written == len(INDICATORS) - 1

    def test_default_observation_start_is_lookback_window_before_today(self, mocker):
        mocker.patch(
            "marketmind_ai.ingestion.fred_pipeline.get_api_key", return_value="fake-key"
        )
        mock_fetch = mocker.patch(
            "marketmind_ai.ingestion.fred_pipeline._fetch_observations",
            return_value=_make_payload([]),
        )
        mocker.patch(
            "marketmind_ai.ingestion.fred_pipeline._parse_observations", return_value=[]
        )
        mock_get_session = mocker.patch("marketmind_ai.ingestion.fred_pipeline.get_session")
        mock_get_session.return_value.__enter__.return_value = mocker.MagicMock()
        tracker = mocker.Mock(rows_written=0)
        mock_ingestion_run = mocker.patch("marketmind_ai.ingestion.fred_pipeline.ingestion_run")
        mock_ingestion_run.return_value.__enter__.return_value = tracker

        fixed_today = date(2026, 9, 6)
        mocker.patch("marketmind_ai.ingestion.fred_pipeline._today", return_value=fixed_today)

        run()

        from marketmind_ai.ingestion.fred_pipeline import _OBSERVATION_LOOKBACK_DAYS

        expected_start = fixed_today - __import__("datetime").timedelta(
            days=_OBSERVATION_LOOKBACK_DAYS
        )
        for call in mock_fetch.call_args_list:
            args = call.args
            assert args[2] == fixed_today  # today
            assert args[3] == expected_start  # observation_start
