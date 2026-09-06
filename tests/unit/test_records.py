"""Test unitari per le interfacce Pydantic in `schemas/records.py`.

Nessun accesso a DB o rete: sono classi Pydantic pure, il test verifica solo
la validazione (campi obbligatori, default degli opzionali, `Literal`).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from marketmind_ai.schemas import (
    AssetRecord,
    CompanyEventRecord,
    MacroEventRecord,
    MarketPriceRecord,
    NewsEventRecord,
    UniverseMemberRecord,
)

FETCHED_AT = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


class TestAssetRecord:
    def test_valid_construction(self):
        record = AssetRecord(
            symbol="AAPL",
            name="Apple Inc.",
            sector="Technology",
            asset_type="equity",
            source="yfinance",
            fetched_at=FETCHED_AT,
        )
        assert record.symbol == "AAPL"
        assert record.sector == "Technology"

    def test_sector_defaults_to_none_when_omitted(self):
        record = AssetRecord(
            symbol="AAPL",
            name="Apple Inc.",
            asset_type="equity",
            source="yfinance",
            fetched_at=FETCHED_AT,
        )
        assert record.sector is None

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            AssetRecord(
                name="Apple Inc.",
                asset_type="equity",
                source="yfinance",
                fetched_at=FETCHED_AT,
            )


class TestMarketPriceRecord:
    def test_valid_construction(self):
        record = MarketPriceRecord(
            symbol="AAPL",
            ts=FETCHED_AT,
            open=100.0,
            high=105.0,
            low=99.5,
            close=104.0,
            volume=123456,
            source="yfinance",
            fetched_at=FETCHED_AT,
        )
        assert record.close == 104.0
        assert record.volume == 123456

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            MarketPriceRecord(
                symbol="AAPL",
                ts=FETCHED_AT,
                open=100.0,
                high=105.0,
                low=99.5,
                # close mancante
                volume=123456,
                source="yfinance",
                fetched_at=FETCHED_AT,
            )


class TestNewsEventRecord:
    def test_valid_construction(self):
        record = NewsEventRecord(
            source="finnhub",
            ts=FETCHED_AT,
            symbol="AAPL",
            headline="Apple annuncia nuovo prodotto",
            raw_payload={"id": 1},
            sentiment_score=0.42,
            url="https://example.com/news/1",
            fetched_at=FETCHED_AT,
        )
        assert record.symbol == "AAPL"
        assert record.sentiment_score == 0.42

    def test_symbol_and_sentiment_score_default_to_none_when_omitted(self):
        # GDELT non fornisce un mapping diretto articolo -> ticker: symbol e
        # sentiment_score sono opzionali per questo motivo (vedi docstring).
        record = NewsEventRecord(
            source="gdelt-ngrams",
            ts=FETCHED_AT,
            headline="Mercati in rialzo",
            raw_payload={"quadgram": "..."},
            url="https://example.com/news/2",
            fetched_at=FETCHED_AT,
        )
        assert record.symbol is None
        assert record.sentiment_score is None

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            NewsEventRecord(
                source="finnhub",
                ts=FETCHED_AT,
                headline="Apple annuncia nuovo prodotto",
                raw_payload={"id": 1},
                fetched_at=FETCHED_AT,
                # url mancante
            )


class TestMacroEventRecord:
    def test_valid_construction(self):
        record = MacroEventRecord(
            indicator="CPIAUCSL",
            ts=date(2026, 8, 1),
            value=314.5,
            source="fred-alfred",
            fetched_at=FETCHED_AT,
        )
        assert record.value == 314.5

    def test_value_defaults_to_none_when_omitted(self):
        # value nullo se il dato non è ancora pubblicato ("." nella risposta
        # grezza FRED) — vedi docstring della classe.
        record = MacroEventRecord(
            indicator="CPIAUCSL",
            ts=date(2026, 8, 1),
            source="fred-alfred",
            fetched_at=FETCHED_AT,
        )
        assert record.value is None

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            MacroEventRecord(
                ts=date(2026, 8, 1),
                value=314.5,
                source="fred-alfred",
                fetched_at=FETCHED_AT,
                # indicator mancante
            )


class TestCompanyEventRecord:
    def test_valid_construction(self):
        record = CompanyEventRecord(
            symbol="AAPL",
            ts=date(2026, 8, 1),
            event_type="earnings",
            raw_payload={"eps": 1.5},
            source="finnhub",
            fetched_at=FETCHED_AT,
        )
        assert record.event_type == "earnings"

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            CompanyEventRecord(
                symbol="AAPL",
                ts=date(2026, 8, 1),
                event_type="earnings",
                source="finnhub",
                fetched_at=FETCHED_AT,
                # raw_payload mancante
            )

    def test_event_type_outside_literal_raises(self):
        with pytest.raises(ValidationError):
            CompanyEventRecord(
                symbol="AAPL",
                ts=date(2026, 8, 1),
                event_type="bankruptcy",  # non in Literal["earnings", "dividend", "split"]
                raw_payload={},
                source="finnhub",
                fetched_at=FETCHED_AT,
            )


class TestUniverseMemberRecord:
    def test_valid_construction(self):
        record = UniverseMemberRecord(
            symbol="SPY",
            name="SPDR S&P 500 ETF Trust",
            is_benchmark=True,
            source="universe-csv",
            fetched_at=FETCHED_AT,
        )
        assert record.is_benchmark is True

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            UniverseMemberRecord(
                symbol="SPY",
                name="SPDR S&P 500 ETF Trust",
                source="universe-csv",
                fetched_at=FETCHED_AT,
                # is_benchmark mancante
            )
