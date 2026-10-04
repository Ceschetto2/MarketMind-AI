"""Test unitari di `yfinance_assets_pipeline.py`: nessuna rete né DB."""

from __future__ import annotations

from datetime import datetime, timezone

from marketmind_pipelines.records import AssetRecord
from marketmind_pipelines.yfinance_assets_pipeline import YFinanceAssetsPipeline, info_to_record

_NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _info(**overrides) -> dict:
    info = {"symbol": "AAPL", "longName": "Apple Inc.", "sector": "Technology", "quoteType": "EQUITY"}
    info.update(overrides)
    return info


class TestInfoToRecord:
    def test_mappa_campi_e_asset_type_minuscolo(self):
        record = info_to_record(_info(), _NOW)

        assert isinstance(record, AssetRecord)
        assert (record.symbol, record.name, record.sector) == ("AAPL", "Apple Inc.", "Technology")
        assert record.asset_type == "equity"
        assert record.source == "yfinance"

    def test_etf_senza_settore(self):
        """Un ETF come SPY non ha settore GICS: `None`, non stringa vuota."""
        record = info_to_record(
            _info(symbol="SPY", longName="SPDR S&P 500 ETF Trust", sector=None, quoteType="ETF"), _NOW
        )

        assert record.sector is None
        assert record.asset_type == "etf"


class TestPipeline:
    def test_extract_legge_info(self, mocker):
        ticker = mocker.patch("marketmind_pipelines.yfinance_assets_pipeline.yf.Ticker")
        ticker.return_value.info = _info()

        raw = YFinanceAssetsPipeline(db=None, symbols=["AAPL"], sleep=lambda _: None).extract("AAPL")

        assert raw["longName"] == "Apple Inc."

    def test_transform_un_record_per_ticker(self):
        records = YFinanceAssetsPipeline(db=None, symbols=["AAPL"]).transform("AAPL", _info())

        assert [r.symbol for r in records] == ["AAPL"]
