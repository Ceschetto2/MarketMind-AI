"""Accesso HTTP condiviso dalle pipeline: timeout obbligatorio e retry mirato.

`HttpSource` sostituisce i `requests.get(...)` sparsi nelle singole pipeline,
che avevano due difetti comuni: nessun timeout (un server che non risponde
bloccava il container a tempo indefinito) e un retry su qualunque eccezione,
anche su un 401/402/403 che riprovando non cambia — solo budget di
richieste consumato (il test FMP restava appeso oltre dieci minuti in
retry su risposte 402). Qui si riprova solo ciò che può risolversi da sé:
errori di rete, timeout, 429 e 5xx.

`call_with_retry` copre le fonti che non passano da `requests` (yfinance),
dove gli errori non sono classificabili per status code.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

import requests
from tenacity import (
    Retrying,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from marketmind_common.config import get_api_key

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

Sleep = Callable[[float], None]


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in RETRYABLE_STATUS
    return False


def _retrying(retry, max_attempts: int, sleep: Sleep) -> Retrying:
    return Retrying(
        retry=retry,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        sleep=sleep,
        reraise=True,
        before_sleep=lambda state: logger.warning(
            "tentativo %d fallito (%s), riprovo", state.attempt_number, state.outcome.exception()
        ),
    )


def call_with_retry[T](
    fn: Callable[[], T],
    *,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    max_attempts: int = 4,
    sleep: Sleep = time.sleep,
) -> T:
    """Esegue `fn` con backoff esponenziale sulle eccezioni `retry_on`."""
    return _retrying(retry_if_exception_type(retry_on), max_attempts, sleep)(fn)


class HttpSource:
    """Client HTTP di una fonte dati.

    `api_key_env`/`api_key_param`: la chiave è letta dall'ambiente solo alla
    prima richiesta (così costruire la pipeline non richiede la chiave, es.
    nei test) e aggiunta ai parametri con il nome che la fonte si aspetta
    (`token` per Finnhub, `apikey` per FMP, `api_key` per FRED).
    """

    # (connessione, lettura) in secondi.
    DEFAULT_TIMEOUT = (10.0, 30.0)

    def __init__(
        self,
        base_url: str = "",
        *,
        api_key_env: str | None = None,
        api_key_param: str | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        max_attempts: int = 4,
        session: requests.Session | None = None,
        sleep: Sleep = time.sleep,
    ) -> None:
        if (api_key_env is None) != (api_key_param is None):
            raise ValueError("api_key_env e api_key_param vanno indicati insieme")
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.api_key_param = api_key_param
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._session = session or requests.Session()
        if headers:
            self._session.headers.update(headers)
        self._sleep = sleep
        self._api_key: str | None = None

    def get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        allow_404: bool = False,
    ) -> requests.Response | None:
        """`GET` con retry sugli errori transitori. Con `allow_404=True` un
        404 restituisce `None` invece di sollevare (es. un file GDELT non
        ancora pubblicato per quel minuto: normale, non un errore)."""
        url = path if path.startswith(("http://", "https://")) else f"{self.base_url}{path}"
        full_params = dict(params or {})
        if self.api_key_param is not None:
            full_params[self.api_key_param] = self._key()

        def attempt() -> requests.Response | None:
            try:
                response = self._session.get(url, params=full_params, timeout=self.timeout)
                if allow_404 and response.status_code == 404:
                    return None
                response.raise_for_status()
            except requests.RequestException as exc:
                raise self._redacted(exc) from None
            return response

        return _retrying(retry_if_exception(_is_transient), self.max_attempts, self._sleep)(attempt)

    def get_json(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        return self.get(path, params=params).json()

    def _redacted(self, exc: requests.RequestException) -> requests.RequestException:
        """La chiave è un parametro dell'URL, che `requests` riporta nel
        messaggio dell'eccezione: senza questa sostituzione finirebbe nei log
        e in `t_ingestion_runs.error_message`. Stesso tipo di eccezione
        (il retry e i chiamanti continuano a distinguerla) e stessa
        `response`."""
        if self._api_key is None or self._api_key not in str(exc):
            return exc
        message = str(exc).replace(self._api_key, "***")
        return type(exc)(message, response=exc.response, request=exc.request)

    def _key(self) -> str:
        if self._api_key is None:
            self._api_key = get_api_key(self.api_key_env)
        return self._api_key
