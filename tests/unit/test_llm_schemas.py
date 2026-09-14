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
        decision = Decision(
            decision="BUY", confidence=0.8, reasoning="momentum positivo", size_pct=0.2
        )

        assert decision.decision == "BUY"
        assert decision.confidence == 0.8
        assert decision.reasoning == "momentum positivo"
        assert decision.size_pct == 0.2

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
            Decision(decision="SELL", confidence=confidence, size_pct=0.5)


class TestDecisionSizePct:
    def test_hold_defaults_size_pct_to_none(self):
        decision = Decision(decision="HOLD")

        assert decision.size_pct is None

    @pytest.mark.parametrize("decision_value", ["BUY", "SELL"])
    def test_requires_size_pct_for_buy_and_sell(self, decision_value):
        with pytest.raises(ValidationError):
            Decision(decision=decision_value)

    def test_forbids_size_pct_for_hold(self):
        with pytest.raises(ValidationError):
            Decision(decision="HOLD", size_pct=0.1)

    @pytest.mark.parametrize("size_pct", [-0.1, 1.1])
    def test_rejects_size_pct_outside_unit_interval(self, size_pct):
        with pytest.raises(ValidationError):
            Decision(decision="BUY", size_pct=size_pct)

    def test_accepts_zero_size_pct(self):
        decision = Decision(decision="SELL", size_pct=0.0)

        assert decision.size_pct == 0.0


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
