"""Test unitari di `finnhub_earnings_pipeline.py`: `HttpSource` è un mock."""

from __future__ import annotations

from datetime import date, datetime, timezone

from marketmind_pipelines.finnhub_earnings_pipeline import FinnhubEarningsPipeline, events_to_records
from marketmind_pipelines.http import HttpSource

_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _event(symbol: str = "AAPL", **overrides) -> dict:
    event = {"symbol": symbol, "date": "2026-09-10", "epsEstimate": 1.5, "hour": "amc", "quarter": 3, "year": 2026}
    event.update(overrides)
    return event


class TestEventsToRecords:
    def test_mappa_i_campi_per_i_simboli_dellUniverso(self):
        [record] = events_to_records([_event()], {"AAPL"}, _NOW)

        assert record.symbol == "AAPL"
        assert record.ts == date(2026, 9, 10)
        assert record.event_type == "earnings"
        assert record.source == "Finnhub"
        assert record.raw_payload["epsEstimate"] == 1.5

    def test_scarta_i_simboli_fuori_universo(self):
        records = events_to_records([_event("AAPL"), _event("ZZZZ")], {"AAPL"}, _NOW)

        assert [r.symbol for r in records] == ["AAPL"]

    def test_nessun_evento(self):
        assert events_to_records([], {"AAPL"}, _NOW) == []


class TestPipeline:
    def test_fetch_su_tutto_il_calendario_senza_symbol(self, mocker):
        http = mocker.MagicMock(spec=HttpSource)
        http.get_json.return_value = {"earningsCalendar": [_event()]}

        events = FinnhubEarningsPipeline(db=None, http=http).fetch()

        assert events == [_event()]
        path = http.get_json.call_args.args[0]
        params = http.get_json.call_args.kwargs["params"]
        assert path == "/calendar/earnings"
        assert set(params) == {"from", "to"}

    def test_risposta_senza_calendario(self, mocker):
        http = mocker.MagicMock(spec=HttpSource)
        http.get_json.return_value = {}

        assert FinnhubEarningsPipeline(db=None, http=http).fetch() == []

    def test_parse_filtra_sullUniverso_letto_in_setup(self):
        pipeline = FinnhubEarningsPipeline(db=None, http=None)
        pipeline.universe = {"MSFT"}

        assert [r.symbol for r in pipeline.parse([_event("AAPL"), _event("MSFT")])] == ["MSFT"]
