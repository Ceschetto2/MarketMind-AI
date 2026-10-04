"""Test unitari di `HttpSource` e `call_with_retry` (`marketmind_pipelines.http`).

La sessione `requests` è un mock e il `sleep` del retry è disattivato: nessuna
rete, nessuna attesa reale.
"""

from __future__ import annotations

import pytest
import requests

from marketmind_pipelines.http import HttpSource, call_with_retry


def _response(status: int, payload=None, content: bytes = b"") -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = content if payload is None else __import__("json").dumps(payload).encode()
    response.url = "https://api.test/x"
    return response


@pytest.fixture
def session(mocker):
    return mocker.MagicMock(spec=requests.Session)


def _source(session, **kwargs) -> HttpSource:
    kwargs.setdefault("sleep", lambda _: None)
    return HttpSource("https://api.test", session=session, **kwargs)


class TestRichiesta:
    def test_url_parametri_e_timeout(self, session):
        session.get.return_value = _response(200, {"ok": True})

        assert _source(session).get_json("/v1/x", params={"a": 1}) == {"ok": True}

        call = session.get.call_args
        assert call.args[0] == "https://api.test/v1/x"
        assert call.kwargs["params"] == {"a": 1}
        assert call.kwargs["timeout"] == HttpSource.DEFAULT_TIMEOUT

    def test_url_assoluto_non_prefissato(self, session):
        session.get.return_value = _response(200, [])

        _source(session).get_json("https://altro.test/y")

        assert session.get.call_args.args[0] == "https://altro.test/y"

    def test_api_key_aggiunta_come_parametro(self, session, mocker):
        mocker.patch("marketmind_pipelines.http.get_api_key", return_value="segreta")
        session.get.return_value = _response(200, [])

        _source(session, api_key_env="X_API_KEY", api_key_param="token").get_json("/x", params={"a": 1})

        assert session.get.call_args.kwargs["params"] == {"a": 1, "token": "segreta"}

    def test_api_key_letta_solo_alla_prima_richiesta(self, session, mocker):
        get_key = mocker.patch("marketmind_pipelines.http.get_api_key", return_value="k")
        source = _source(session, api_key_env="X_API_KEY", api_key_param="token")
        get_key.assert_not_called()

        session.get.return_value = _response(200, [])
        source.get_json("/x")
        source.get_json("/x")

        get_key.assert_called_once_with("X_API_KEY")

    def test_api_key_env_e_param_vanno_insieme(self, session):
        with pytest.raises(ValueError):
            _source(session, api_key_env="X_API_KEY")

    def test_404_ammesso_restituisce_none(self, session):
        session.get.return_value = _response(404)

        assert _source(session).get("/x", allow_404=True) is None
        session.get.assert_called_once()

    def test_404_non_ammesso_solleva(self, session):
        session.get.return_value = _response(404)

        with pytest.raises(requests.HTTPError):
            _source(session).get("/x")


class TestRedazioneChiave:
    """La chiave viaggia nei parametri dell'URL: senza redazione finirebbe
    nel messaggio dell'eccezione, quindi nei log e in
    `t_ingestion_runs.error_message`."""

    def test_chiave_oscurata_nellerrore_http(self, session, mocker):
        mocker.patch("marketmind_pipelines.http.get_api_key", return_value="segreta123")
        response = _response(402)
        response.url = "https://api.test/x?symbol=AAPL&apikey=segreta123"
        session.get.return_value = response

        with pytest.raises(requests.HTTPError) as info:
            _source(session, api_key_env="K", api_key_param="apikey").get_json("/x")

        assert "segreta123" not in str(info.value)
        assert "apikey=***" in str(info.value)
        assert info.value.response is response

    def test_chiave_oscurata_negli_errori_di_rete(self, session, mocker):
        mocker.patch("marketmind_pipelines.http.get_api_key", return_value="segreta123")
        session.get.side_effect = requests.ConnectionError("errore su https://api.test/x?apikey=segreta123")

        with pytest.raises(requests.ConnectionError) as info:
            _source(session, api_key_env="K", api_key_param="apikey", max_attempts=1).get_json("/x")

        assert "segreta123" not in str(info.value)


class TestRetry:
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_errori_transitori_riprovati(self, session, status):
        session.get.side_effect = [_response(status), _response(200, {"ok": 1})]

        assert _source(session).get_json("/x") == {"ok": 1}
        assert session.get.call_count == 2

    @pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
    def test_errori_client_non_riprovati(self, session, status):
        """Un 402 (endpoint fuori dal piano) o un 401 (chiave sbagliata) non
        cambia riprovando: ripeterlo consuma solo budget di richieste."""
        session.get.return_value = _response(status)

        with pytest.raises(requests.HTTPError):
            _source(session).get_json("/x")
        session.get.assert_called_once()

    def test_errori_di_rete_riprovati(self, session):
        session.get.side_effect = [requests.ConnectionError(), requests.Timeout(), _response(200, [])]

        assert _source(session).get_json("/x") == []
        assert session.get.call_count == 3

    def test_si_arrende_dopo_max_attempts(self, session):
        session.get.return_value = _response(503)

        with pytest.raises(requests.HTTPError):
            _source(session, max_attempts=3).get_json("/x")
        assert session.get.call_count == 3

    def test_attese_crescenti_tra_i_tentativi(self, session):
        waits: list[float] = []
        session.get.return_value = _response(503)

        with pytest.raises(requests.HTTPError):
            _source(session, max_attempts=3, sleep=waits.append).get_json("/x")

        assert len(waits) == 2
        assert waits[0] < waits[1]


class TestCallWithRetry:
    def test_riprova_e_restituisce(self):
        calls = iter([RuntimeError("1"), RuntimeError("2"), "ok"])

        def flaky():
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            return value

        assert call_with_retry(flaky, sleep=lambda _: None) == "ok"

    def test_rilancia_lultima_eccezione(self):
        def always_fails():
            raise RuntimeError("sempre")

        with pytest.raises(RuntimeError, match="sempre"):
            call_with_retry(always_fails, max_attempts=2, sleep=lambda _: None)

    def test_solo_le_eccezioni_indicate(self):
        attempts = []

        def fails():
            attempts.append(1)
            raise KeyError("x")

        with pytest.raises(KeyError):
            call_with_retry(fails, retry_on=(RuntimeError,), sleep=lambda _: None)
        assert len(attempts) == 1
