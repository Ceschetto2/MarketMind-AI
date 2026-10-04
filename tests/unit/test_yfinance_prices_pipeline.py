"""Test unitari di `yfinance_prices_pipeline.py`: nessuna rete né DB,
`yf.Ticker` è mockato. L'isolamento degli errori per ticker è coperto dai
test delle classi base (`test_pipelines_base.py`)."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from marketmind_pipelines.records import MarketPriceRecord
from marketmind_pipelines.yfinance_prices_pipeline import YFinancePricesPipeline, rows_to_records

_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _history() -> pd.DataFrame:
    """Stessa forma di `yf.Ticker(...).history()`: indice timestamp, colonne OHLCV."""
    index = pd.date_range("2026-09-06 09:00", periods=2, freq="60min", tz="UTC")
    return pd.DataFrame(
        {
            "Open": [100.0, 101.5],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.5],
            "Close": [101.0, 102.5],
            "Volume": [1000, 1500],
        },
        index=index,
    )


def _pipeline() -> YFinancePricesPipeline:
    return YFinancePricesPipeline(db=None, symbols=["AAPL"], sleep=lambda _: None)


class TestRowsToRecords:
    def test_mappa_colonne_e_tipi(self):
        records = rows_to_records("AAPL", _history(), _NOW)

        assert len(records) == 2
        first = records[0]
        assert isinstance(first, MarketPriceRecord)
        assert first.symbol == "AAPL"
        assert first.ts == datetime(2026, 9, 6, 9, 0, tzinfo=timezone.utc)
        assert (first.open, first.high, first.low, first.close) == (100.0, 102.0, 99.0, 101.0)
        assert first.volume == 1000 and isinstance(first.volume, int)
        assert first.source == "yfinance"
        assert first.fetched_at == _NOW

    def test_history_vuota_nessun_record(self):
        assert rows_to_records("AAPL", _history().iloc[0:0], _NOW) == []


class TestPipeline:
    def test_extract_chiede_un_giorno_di_barre_orarie(self, mocker):
        ticker = mocker.patch("marketmind_pipelines.yfinance_prices_pipeline.yf.Ticker")
        ticker.return_value.history.return_value = _history()

        _pipeline().extract("AAPL")

        ticker.assert_called_once_with("AAPL")
        ticker.return_value.history.assert_called_once_with(period="1d", interval="60m")

    def test_extract_riprova_e_poi_riesce(self, mocker):
        ticker = mocker.patch("marketmind_pipelines.yfinance_prices_pipeline.yf.Ticker")
        ticker.return_value.history.side_effect = [RuntimeError("429"), _history()]

        assert len(_pipeline().extract("AAPL")) == 2
        assert ticker.return_value.history.call_count == 2

    def test_extract_rilancia_dopo_lultimo_tentativo(self, mocker):
        ticker = mocker.patch("marketmind_pipelines.yfinance_prices_pipeline.yf.Ticker")
        ticker.return_value.history.side_effect = RuntimeError("giù")

        with pytest.raises(RuntimeError):
            _pipeline().extract("AAPL")
        assert ticker.return_value.history.call_count == 4

    def test_transform_history_vuota_non_e_un_errore(self):
        assert _pipeline().transform("AAPL", _history().iloc[0:0]) == []
