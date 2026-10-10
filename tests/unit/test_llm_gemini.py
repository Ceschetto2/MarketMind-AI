"""Test unitari per `llm/gemini.py`.

Nessun accesso a rete: il client `google.genai` è mockato con
`pytest-mock`. Lo scopo dei test è la logica attorno alla chiamata (prompt,
retry, parsing/validazione della risposta), non il client SDK in sé.
"""

from __future__ import annotations

import pytest

from marketmind_llm_decision_engine.llm.exceptions import DecisionError
import httpx
from google.genai import errors

from marketmind_llm_decision_engine.llm.gemini import GeminiProvider, _call_gemini
from marketmind_llm_decision_engine.llm.schemas import Decision, WatchlistSelection


def _mock_response(mocker, text: str):
    response = mocker.Mock()
    response.text = text
    return response


class TestDecide:
    def test_returns_decision_on_valid_response(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        valid_json = (
            '{"decision": "BUY", "confidence": 0.7, "reasoning": "trend positivo", "size_pct": 0.2}'
        )
        mocker.patch(
            "marketmind_llm_decision_engine.llm.gemini._call_gemini",
            return_value=_mock_response(mocker, valid_json),
        )

        decision = provider.decide({"symbol": "AAPL"})

        assert decision == Decision(
            decision="BUY", confidence=0.7, reasoning="trend positivo", size_pct=0.2
        )

    def test_strips_markdown_fence_before_validating(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        fenced_json = (
            '```json\n{"decision": "BUY", "confidence": 0.85, "reasoning": "trend", '
            '"size_pct": 0.3}\n```'
        )
        mocker.patch(
            "marketmind_llm_decision_engine.llm.gemini._call_gemini",
            return_value=_mock_response(mocker, fenced_json),
        )

        decision = provider.decide({"symbol": "AAPL"})

        assert decision == Decision(
            decision="BUY", confidence=0.85, reasoning="trend", size_pct=0.3
        )

    def test_raises_decision_error_on_invalid_json(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        mocker.patch(
            "marketmind_llm_decision_engine.llm.gemini._call_gemini",
            return_value=_mock_response(mocker, "non è json"),
        )

        with pytest.raises(DecisionError):
            provider.decide({"symbol": "AAPL"})

    def test_raises_decision_error_on_schema_mismatch(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        mocker.patch(
            "marketmind_llm_decision_engine.llm.gemini._call_gemini",
            return_value=_mock_response(mocker, '{"decision": "MAYBE"}'),
        )

        with pytest.raises(DecisionError):
            provider.decide({"symbol": "AAPL"})

    def test_raises_decision_error_when_call_fails_after_retries(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        mocker.patch(
            "marketmind_llm_decision_engine.llm.gemini._call_gemini",
            side_effect=RuntimeError("rete non raggiungibile"),
        )

        with pytest.raises(DecisionError):
            provider.decide({"symbol": "AAPL"})

    def test_prompt_includes_serialized_context(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        mock_call = mocker.patch(
            "marketmind_llm_decision_engine.llm.gemini._call_gemini",
            return_value=_mock_response(
                mocker, '{"decision": "HOLD", "confidence": null, "reasoning": null}'
            ),
        )

        provider.decide({"symbol": "AAPL", "decision_ts": "2026-09-01"})

        _, kwargs = mock_call.call_args
        assert "AAPL" in kwargs["prompt"]
        assert "2026-09-01" in kwargs["prompt"]


def _api_error(cls, code: int, status: str):
    return cls(code, {"error": {"code": code, "message": status, "status": status}})


class TestCallGeminiRetry:
    """Si riprova solo ciò che può risolversi da sé: 5xx e errori di rete.
    Un 4xx (429 per quota giornaliera esaurita, 400, 404 di un modello
    inesistente) non cambia riprovando, consumerebbe solo altra quota."""

    @pytest.fixture(autouse=True)
    def _no_sleep(self, mocker):
        mocker.patch.object(_call_gemini.retry, "sleep", lambda _seconds: None)

    def _call(self, client):
        return _call_gemini(client, model="gemini-3.6-flash", prompt="ciao", response_schema=Decision)

    @pytest.mark.parametrize(
        "transient",
        [
            _api_error(errors.ServerError, 500, "INTERNAL"),
            _api_error(errors.ServerError, 504, "DEADLINE_EXCEEDED"),
            httpx.ConnectError("rete non raggiungibile"),
            httpx.ReadTimeout("timeout"),
        ],
        ids=["500", "504", "connessione", "timeout"],
    )
    def test_errori_transitori_riprovati(self, mocker, transient):
        client = mocker.Mock()
        client.models.generate_content.side_effect = [transient, _mock_response(mocker, '{"decision": "HOLD"}')]

        assert self._call(client).text == '{"decision": "HOLD"}'
        assert client.models.generate_content.call_count == 2

    @pytest.mark.parametrize(
        "permanent",
        [
            _api_error(errors.ClientError, 429, "RESOURCE_EXHAUSTED"),  # senza tempo indicato
            _api_error(errors.ClientError, 400, "INVALID_ARGUMENT"),
            _api_error(errors.ClientError, 404, "NOT_FOUND"),
            RuntimeError("bug nel codice"),
        ],
        ids=["429", "400", "404", "altro"],
    )
    def test_errori_permanenti_non_riprovati(self, mocker, permanent):
        client = mocker.Mock()
        client.models.generate_content.side_effect = permanent

        with pytest.raises(type(permanent)):
            self._call(client)
        assert client.models.generate_content.call_count == 1

    def test_si_arrende_dopo_tre_tentativi(self, mocker):
        client = mocker.Mock()
        client.models.generate_content.side_effect = _api_error(errors.ServerError, 500, "INTERNAL")

        with pytest.raises(errors.ServerError):
            self._call(client)
        assert client.models.generate_content.call_count == 3


def _rate_limited(retry_delay: str | None, message_delay: str | None = None, quota_id: str = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"):
    message = "You exceeded your current quota."
    if message_delay is not None:
        message += f" Please retry in {message_delay}s."
    details = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]}]
    if retry_delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    return errors.ClientError(
        429, {"error": {"code": 429, "message": message, "status": "RESOURCE_EXHAUSTED", "details": details}}
    )


class TestRateLimit429:
    """Un 429 con un tempo di attesa indicato da Google fino a 5 minuti (un
    limite al minuto: richieste o token) viene atteso e riprovato; oltre
    (la quota giornaliera, ~10 ore all'azzeramento) o senza un tempo
    indicato resta definitivo."""

    @pytest.fixture
    def waits(self, mocker):
        recorded: list[float] = []
        mocker.patch.object(_call_gemini.retry, "sleep", recorded.append)
        return recorded

    def _call(self, client):
        return _call_gemini(client, model="gemini-3.6-flash", prompt="ciao", response_schema=Decision)

    def test_attende_il_retry_delay_e_riprova(self, mocker, waits):
        client = mocker.Mock()
        client.models.generate_content.side_effect = [_rate_limited("46s"), _mock_response(mocker, '{"decision": "HOLD"}')]

        assert self._call(client).text == '{"decision": "HOLD"}'
        assert waits == [pytest.approx(47.0)]

    def test_retry_delay_frazionario(self, mocker, waits):
        client = mocker.Mock()
        client.models.generate_content.side_effect = [_rate_limited("2.5s"), _mock_response(mocker, '{"decision": "HOLD"}')]

        self._call(client)

        assert waits == [pytest.approx(3.5)]

    def test_senza_retry_info_usa_il_tempo_nel_messaggio(self, mocker, waits):
        client = mocker.Mock()
        client.models.generate_content.side_effect = [
            _rate_limited(None, message_delay="3.075421386"),
            _mock_response(mocker, '{"decision": "HOLD"}'),
        ]

        self._call(client)

        assert waits == [pytest.approx(4.075421386)]

    def test_esattamente_cinque_minuti_attende(self, mocker, waits):
        client = mocker.Mock()
        client.models.generate_content.side_effect = [_rate_limited("300s"), _mock_response(mocker, '{"decision": "HOLD"}')]

        self._call(client)

        assert waits == [pytest.approx(301.0)]

    def test_oltre_cinque_minuti_non_riprova(self, mocker, waits):
        """La quota giornaliera: Google indica l'attesa fino all'azzeramento."""
        client = mocker.Mock()
        client.models.generate_content.side_effect = _rate_limited(
            "36401s", quota_id="GenerateRequestsPerDayPerProjectPerModel-FreeTier"
        )

        with pytest.raises(errors.ClientError):
            self._call(client)
        assert client.models.generate_content.call_count == 1
        assert waits == []

    def test_senza_tempo_indicato_non_riprova(self, mocker, waits):
        client = mocker.Mock()
        client.models.generate_content.side_effect = _rate_limited(None)

        with pytest.raises(errors.ClientError):
            self._call(client)
        assert client.models.generate_content.call_count == 1

    def test_si_arrende_dopo_i_tentativi_massimi(self, mocker, waits):
        client = mocker.Mock()
        client.models.generate_content.side_effect = _rate_limited("5s")

        with pytest.raises(errors.ClientError):
            self._call(client)
        assert client.models.generate_content.call_count == 3
        assert len(waits) == 2


class TestSelectWatchlist:
    def test_returns_selection_on_valid_response(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        valid_json = '{"symbols": ["AAPL", "MSFT"], "reasoning": "focus tech"}'
        mock_call = mocker.patch(
            "marketmind_llm_decision_engine.llm.gemini._call_gemini",
            return_value=_mock_response(mocker, valid_json),
        )

        selection = provider.select_watchlist({"strategy_prompt": "focus tech"})

        assert selection == WatchlistSelection(symbols=["AAPL", "MSFT"], reasoning="focus tech")
        _, kwargs = mock_call.call_args
        assert kwargs["response_schema"] is WatchlistSelection

    def test_raises_decision_error_on_invalid_json(self, mocker):
        provider = GeminiProvider(api_key="fake-key")
        mocker.patch(
            "marketmind_llm_decision_engine.llm.gemini._call_gemini",
            return_value=_mock_response(mocker, "non è json"),
        )

        with pytest.raises(DecisionError):
            provider.select_watchlist({"strategy_prompt": "focus tech"})
