"""Test unitari per `llm/schemas.py`.

Nessun accesso a rete/DB: `Decision` è una classe Pydantic pura, il test
verifica solo la validazione (`Literal`, range di `confidence`, default
degli opzionali).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marketmind_llm_decision_engine.llm.schemas import Decision, DeferralRequest, WatchlistSelection


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
    """Rinvio: l'LLM chiede di essere rivalutato fra N ore, dentro il ciclo in
    corso (cadenza di 7 giorni, quindi al massimo 167 ore). Nessun refresh
    ad-hoc dei dati: le pipeline orarie li aggiornano comunque."""

    def test_valid_construction(self):
        defer = DeferralRequest(recheck_after_hours=24, reason="trimestrale domani")

        assert defer.recheck_after_hours == 24
        assert defer.reason == "trimestrale domani"

    @pytest.mark.parametrize("hours", [0, -5, 168])
    def test_rejects_delay_outside_the_cycle(self, hours):
        with pytest.raises(ValidationError):
            DeferralRequest(recheck_after_hours=hours, reason="r")

    def test_reason_is_required(self):
        with pytest.raises(ValidationError):
            DeferralRequest(recheck_after_hours=2)

    def test_no_refresh_hints_anymore(self):
        assert set(DeferralRequest.model_fields) == {"recheck_after_hours", "reason"}

    def test_decision_accepts_defer(self):
        decision = Decision(
            decision="HOLD",
            reasoning="in attesa dell'annuncio earnings",
            defer=DeferralRequest(recheck_after_hours=24, reason="earnings domani"),
        )

        assert decision.defer.recheck_after_hours == 24


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
