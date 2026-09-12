"""Test unitari per la logica pura di `yfinance_assets_pipeline.py`.

Nessun accesso a rete/DB reale: `yfinance.Ticker` e lo strato di scrittura
sono mockati con `pytest-mock`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from marketmind_ai.ingestion.yfinance_assets_pipeline import _fetch_info, _info_to_record, run
from marketmind_ai.schemas import AssetRecord


def _make_info(**overrides) -> dict:
    info = {
        "symbol": "AAPL",
        "longName": "Apple Inc.",
        "sector": "Technology",
        "quoteType": "EQUITY",
        "currentPrice": 320.0,  # campo non mappato, deve essere ignorato
    }
    info.update(overrides)
    return info


class TestInfoToRecord:
    def test_maps_fields_and_lowercases_asset_type(self):
        record = _info_to_record(_make_info())

        assert isinstance(record, AssetRecord)
        assert record.symbol == "AAPL"
        assert record.name == "Apple Inc."
        assert record.sector == "Technology"
        assert record.asset_type == "equity"
        assert record.source == "yfinance"

    def test_etf_has_no_sector(self):
        """Un ETF come SPY non ha un settore GICS — `.info` lo restituisce
        `None`/assente, va mappato a `None`, non a una stringa vuota."""
        record = _info_to_record(
            _make_info(symbol="SPY", longName="SPDR S&P 500 ETF Trust", sector=None, quoteType="ETF")
        )

        assert record.sector is None
        assert record.asset_type == "etf"


class TestRun:
    def test_asset_not_found_is_impossible_here_but_fetch_failure_does_not_block_others(
        self, mocker
    ):
        """A differenza delle altre pipeline, qui non c'è `AssetNotFoundError`
        da gestire (questa pipeline *crea* l'anagrafica, non la presuppone) —
        il caso da testare è un fetch fallito per un ticker che non deve
        bloccare gli altri."""
        mocker.patch(
            "marketmind_ai.ingestion.yfinance_assets_pipeline.get_universe_symbols",
            return_value=["AAA", "BBB"],
        )
        mocker.patch("marketmind_ai.ingestion.yfinance_assets_pipeline.time.sleep")
        mocker.patch.object(_fetch_info.retry, "sleep", lambda _seconds: None)

        ticker_bbb = mocker.Mock(info=_make_info(symbol="BBB"))

        def ticker_side_effect(symbol):
            if symbol == "AAA":
                raise Exception("fetch fallito")
            return ticker_bbb

        mocker.patch(
            "marketmind_ai.ingestion.yfinance_assets_pipeline.yf.Ticker",
            side_effect=ticker_side_effect,
        )

        mock_session = mocker.MagicMock(name="session")
        mock_get_session = mocker.patch(
            "marketmind_ai.ingestion.yfinance_assets_pipeline.get_session"
        )
        mock_get_session.return_value.__enter__.return_value = mock_session

        mock_upsert_asset = mocker.patch(
            "marketmind_ai.ingestion.yfinance_assets_pipeline.upsert_asset"
        )

        tracker = mocker.Mock(rows_written=0)
        mock_ingestion_run = mocker.patch(
            "marketmind_ai.ingestion.yfinance_assets_pipeline.ingestion_run"
        )
        mock_ingestion_run.return_value.__enter__.return_value = tracker

        run()

        assert mock_upsert_asset.call_count == 1
        assert tracker.rows_written == 1
