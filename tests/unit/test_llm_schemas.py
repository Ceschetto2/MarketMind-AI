"""Test unitari per `llm/schemas.py`.

Nessun accesso a rete/DB: `Decision` è una classe Pydantic pura, il test
verifica solo la validazione (`Literal`, range di `confidence`, default
degli opzionali).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marketmind_ai.llm.schemas import Decision


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

    def test_rejects_decision_outside_enum(self):
        with pytest.raises(ValidationError):
            Decision(decision="BUY_A_LOT")

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_rejects_confidence_outside_unit_interval(self, confidence):
        with pytest.raises(ValidationError):
            Decision(decision="SELL", confidence=confidence)
