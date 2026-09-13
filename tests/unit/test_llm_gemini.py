"""Test unitari per `llm/gemini.py`.

Nessun accesso a rete: il client `google.genai` è mockato con
`pytest-mock`. Lo scopo dei test è la logica attorno alla chiamata (prompt,
retry, parsing/validazione della risposta), non il client SDK in sé.
"""

from __future__ import annotations

import pytest

from marketmind_ai.llm.exceptions import DecisionError
from marketmind_ai.llm.gemini import GeminiProvider, _call_gemini
from marketmind_ai.llm.schemas import Decision


def _mock_response(mocker, text: str):
    response = mocker.Mock()
    response.text = text
    return response


class TestDecide:
    def test_returns_decision_on_valid_response(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        valid_json = '{"decision": "BUY", "confidence": 0.7, "reasoning": "trend positivo"}'
        mocker.patch(
            "marketmind_ai.llm.gemini._call_gemini",
            return_value=_mock_response(mocker, valid_json),
        )

        decision = provider.decide({"symbol": "AAPL"})

        assert decision == Decision(decision="BUY", confidence=0.7, reasoning="trend positivo")

    def test_strips_markdown_fence_before_validating(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        fenced_json = (
            '```json\n{"decision": "BUY", "confidence": 0.85, "reasoning": "trend"}\n```'
        )
        mocker.patch(
            "marketmind_ai.llm.gemini._call_gemini",
            return_value=_mock_response(mocker, fenced_json),
        )

        decision = provider.decide({"symbol": "AAPL"})

        assert decision == Decision(decision="BUY", confidence=0.85, reasoning="trend")

    def test_raises_decision_error_on_invalid_json(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        mocker.patch(
            "marketmind_ai.llm.gemini._call_gemini",
            return_value=_mock_response(mocker, "non è json"),
        )

        with pytest.raises(DecisionError):
            provider.decide({"symbol": "AAPL"})

    def test_raises_decision_error_on_schema_mismatch(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        mocker.patch(
            "marketmind_ai.llm.gemini._call_gemini",
            return_value=_mock_response(mocker, '{"decision": "MAYBE"}'),
        )

        with pytest.raises(DecisionError):
            provider.decide({"symbol": "AAPL"})

    def test_raises_decision_error_when_call_fails_after_retries(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        mocker.patch(
            "marketmind_ai.llm.gemini._call_gemini",
            side_effect=RuntimeError("rete non raggiungibile"),
        )

        with pytest.raises(DecisionError):
            provider.decide({"symbol": "AAPL"})

    def test_prompt_includes_serialized_context(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        mock_call = mocker.patch(
            "marketmind_ai.llm.gemini._call_gemini",
            return_value=_mock_response(
                mocker, '{"decision": "HOLD", "confidence": null, "reasoning": null}'
            ),
        )

        provider.decide({"symbol": "AAPL", "decision_ts": "2026-09-01"})

        _, kwargs = mock_call.call_args
        assert "AAPL" in kwargs["prompt"]
        assert "2026-09-01" in kwargs["prompt"]


class TestCallGemiRetry:
    def test_retries_and_eventually_succeeds(self, mocker):
        mock_client = mocker.Mock()
        mock_client.models.generate_content.side_effect = [
            RuntimeError("transitorio"),
            RuntimeError("transitorio"),
            _mock_response(mocker, '{"decision": "HOLD"}'),
        ]
        mocker.patch.object(_call_gemini.retry, "sleep", lambda _seconds: None)

        response = _call_gemini(mock_client, model="gemini-2.5-flash", prompt="ciao")

        assert response.text == '{"decision": "HOLD"}'
        assert mock_client.models.generate_content.call_count == 3

    def test_propagates_after_stop_after_attempt(self, mocker):
        mock_client = mocker.Mock()
        mock_client.models.generate_content.side_effect = RuntimeError("transitorio")
        mocker.patch.object(_call_gemini.retry, "sleep", lambda _seconds: None)

        with pytest.raises(RuntimeError):
            _call_gemini(mock_client, model="gemini-2.5-flash", prompt="ciao")
