"""Test unitari di `fred_pipeline.py`: `HttpSource` è un mock."""

from __future__ import annotations

from datetime import date, datetime, timezone

from marketmind_pipelines.fred_pipeline import (
    FRED_OBSERVATIONS_URL,
    INDICATORS,
    SOURCE,
    FredPipeline,
    parse_observations,
)
from marketmind_pipelines.http import HttpSource

_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _payload(observations: list[dict]) -> dict:
    return {"realtime_start": "2026-09-06", "realtime_end": "2026-09-06", "observations": observations}


class TestParseObservations:
    def test_mappa_i_campi(self):
        [record] = parse_observations("UNRATE", _payload([{"date": "2026-08-01", "value": "4.2"}]), _NOW)

        assert (record.indicator, record.ts, record.value) == ("UNRATE", date(2026, 8, 1), 4.2)
        assert record.source == SOURCE
        assert record.fetched_at == _NOW

    def test_punto_e_valore_non_pubblicato(self):
        [record] = parse_observations("GDP", _payload([{"date": "2026-07-01", "value": "."}]), _NOW)

        assert record.value is None

    def test_piu_osservazioni(self):
        records = parse_observations(
            "UNRATE",
            _payload([{"date": "2026-07-01", "value": "4.1"}, {"date": "2026-08-01", "value": "4.2"}]),
            _NOW,
        )

        assert [r.value for r in records] == [4.1, 4.2]

    def test_nessuna_osservazione(self):
        assert parse_observations("UNRATE", {}, _NOW) == []


class TestPipeline:
    def test_un_target_per_indicatore(self):
        assert FredPipeline(db=None, http=None).targets() == INDICATORS

    def test_alfred_realtime_fissato_a_oggi(self, mocker):
        """`realtime_start`/`realtime_end` a *oggi*, mai lasciati di default:
        il punto non negoziabile dell'onboarding FRED (no-look-ahead)."""
        http = mocker.MagicMock(spec=HttpSource)
        http.get_json.return_value = _payload([])
        pipeline = FredPipeline(db=None, http=http)
        pipeline.today = date(2026, 9, 6)

        pipeline.extract("UNRATE")

        url = http.get_json.call_args.args[0]
        params = http.get_json.call_args.kwargs["params"]
        assert url == FRED_OBSERVATIONS_URL
        assert params == {
            "series_id": "UNRATE",
            "file_type": "json",
            "realtime_start": "2026-09-06",
            "realtime_end": "2026-09-06",
            "observation_start": "2026-07-08",
        }

    def test_client_di_default_con_api_key_fred(self):
        http = FredPipeline(db=None).http

        assert (http.api_key_env, http.api_key_param) == ("FRED_API_KEY", "api_key")
