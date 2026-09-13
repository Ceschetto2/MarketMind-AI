"""Test unitari per `llm/schemas.py`.

Nessun accesso a rete/DB: `Decision` è una classe Pydantic pura, il test
verifica solo la validazione (`Literal`, range di `confidence`, default
degli opzionali).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marketmind_ai.llm.schemas import Decision, DeferralRequest, WatchlistSelection


class TestDecision:
    def test_valid_construction(self):
        decision = Decision(decision="BUY", confidence=0.8, reasoning="momentum positivo")

        assert decision.decision == "BUY"
        assert decision.confidence == 0.8
        assert decision.reasoning == "momentum positivo"

    def test_confidence_and_reasoning_default_to_none(self):
        decision = Decision(decision="HOLD")

        assert decision.confidence is None
        assert decision.reasoning is None

    def test_defer_defaults_to_none(self):
        decision = Decision(decision="HOLD")

        assert decision.defer is None

    def test_rejects_decision_outside_enum(self):
        with pytest.raises(ValidationError):
            Decision(decision="BUY_A_LOT")

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_rejects_confidence_outside_unit_interval(self, confidence):
        with pytest.raises(ValidationError):
            Decision(decision="SELL", confidence=confidence)


class TestDeferralRequest:
    def test_valid_construction_with_refresh_hints(self):
        defer = DeferralRequest(
            retry_after_minutes=15,
            refresh_pipeline="finnhub-news",
            refresh_symbol="AAPL",
        )

        assert defer.retry_after_minutes == 15
        assert defer.refresh_pipeline == "finnhub-news"
        assert defer.refresh_symbol == "AAPL"

    def test_refresh_hints_default_to_none(self):
        defer = DeferralRequest(retry_after_minutes=30)

        assert defer.refresh_pipeline is None
        assert defer.refresh_symbol is None

    @pytest.mark.parametrize("retry_after_minutes", [0, -5])
    def test_rejects_non_positive_delay(self, retry_after_minutes):
        with pytest.raises(ValidationError):
            DeferralRequest(retry_after_minutes=retry_after_minutes)

    def test_decision_accepts_defer(self):
        decision = Decision(
            decision="HOLD",
            reasoning="in attesa dell'annuncio earnings",
            defer=DeferralRequest(retry_after_minutes=15, refresh_pipeline="finnhub-news"),
        )

        assert decision.defer.retry_after_minutes == 15
        assert decision.defer.refresh_pipeline == "finnhub-news"


class TestWatchlistSelection:
    def test_valid_construction(self):
        selection = WatchlistSelection(symbols=["AAPL", "MSFT"], reasoning="focus tech")

        assert selection.symbols == ["AAPL", "MSFT"]
        assert selection.reasoning == "focus tech"

    def test_reasoning_defaults_to_none(self):
        selection = WatchlistSelection(symbols=["AAPL"])

        assert selection.reasoning is None

    def test_empty_symbols_is_valid(self):
        selection = WatchlistSelection(symbols=[])

        assert selection.symbols == []
