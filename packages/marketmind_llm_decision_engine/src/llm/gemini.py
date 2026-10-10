"""Provider LLM per Gemini, dietro l'interfaccia generica di `base.py`.

SDK nativo `google-genai`, senza tool-use/function-calling: né la decisione
BUY/SELL/HOLD né il bootstrap del portfolio ne hanno bisogno, il
function-calling per il retrieval agentic (§2.4 di `Market Mind AI.md`) è
rimandato a quando servirà davvero. `response_schema` chiede output
strutturato lato API (`Decision` per `decide()`, `WatchlistSelection` per
`select_watchlist()`), ma il parsing/validazione Pydantic locale su
`response.text` resta comunque il controllo finale — non ci si fida
ciecamente del rispetto dello schema lato provider.

Nonostante il nome, `GeminiProvider` funziona anche con modelli Gemma
(stesso client `google-genai`, stesso `response_schema`): `model` è un
parametro del costruttore, non hardcoded a chiamata — verificato con
`gemma-4-31b-it`, che però non rispetta `response_mime_type="application/
json"` con la stessa affidabilità dei modelli Gemini propriamente detti
(vedi `_strip_markdown_fence`).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError
from tenacity import RetryCallState, retry, retry_if_exception, stop_after_attempt, wait_exponential

from marketmind_common.config import get_api_key
from marketmind_llm_decision_engine.llm.exceptions import DecisionError
from marketmind_llm_decision_engine.llm.schemas import Decision, WatchlistSelection

logger = logging.getLogger(__name__)

_SchemaT = TypeVar("_SchemaT", bound=BaseModel)

# gemini-2.5-flash non è più disponibile ai nuovi utenti (404 dall'API,
# scoperto in un test end-to-end reale, non dai test a priori — mockano il
# client, non convalidano il nome modello contro l'API vera).
_DEFAULT_MODEL = "gemini-3.6-flash"

# Nessun timeout di default nell'SDK: una chiamata senza risposta (osservato
# con un modello Gemma, restato appeso ~9h43m in un test end-to-end prima di
# essere terminato a mano) altrimenti blocca il processo a tempo indefinito
# — inaccettabile su un run settimanale con centinaia di chiamate.
_REQUEST_TIMEOUT_MS = 30_000

_DECISION_SYSTEM_PROMPT = (
    "Sei il motore decisionale di una piattaforma di simulazione finanziaria. "
    "In base esclusivamente al contesto fornito qui sotto — nessuna "
    "informazione oltre il timestamp di decisione in esso contenuto — "
    "decidi se BUY, SELL o HOLD per l'asset descritto. La decisione è "
    "specifica per il portfolio indicato in `portfolio`: tieni conto del "
    "cash disponibile (BUY non ha senso se non c'è capitale libero), delle "
    "posizioni correnti (SELL non ha senso su un asset non posseduto; una "
    "posizione già ampia sullo stesso asset è un motivo per preferire HOLD "
    "anche a fronte di un segnale di mercato positivo) e della strategia "
    "indicata in `portfolio.strategy_prompt`, che guida ogni giudizio. "
    "Se decidi BUY o SELL, la tua decisione verrà eseguita per davvero, "
    "subito: includi sempre `size_pct` (un numero tra 0 e 1) — per un BUY, "
    "la percentuale del cash disponibile del portfolio da investire in "
    "questo asset; per un SELL, la percentuale della posizione corrente da "
    "liquidare. Sii prudente: valuta il numero di asset ancora da decidere "
    "in questo stesso giro e la strategia del portfolio, non allocare una "
    "size che lascerebbe il portfolio sbilanciato su un singolo asset. Se "
    "decidi HOLD, ometti `size_pct` — non ha senso se non fai nulla. "
    "Rispondi solo con l'oggetto JSON richiesto dallo schema, nessun testo "
    "aggiuntivo."
)

_WATCHLIST_SYSTEM_PROMPT = (
    "Stai facendo il bootstrap di un portfolio per una piattaforma di "
    "simulazione finanziaria: scegli quali asset dell'universo fornito "
    "questo portfolio dovrà osservare da qui in avanti, in base alla "
    "strategia indicata in `strategy_prompt`. Non stai decidendo se "
    "comprare o vendere — nessun capitale viene impegnato da questa scelta, "
    "solo lo scope su cui il motore deciderà settimana per settimana. "
    "Restituisci solo i `symbol` presenti nell'universo fornito. Rispondi "
    "solo con l'oggetto JSON richiesto dallo schema, nessun testo "
    "aggiuntivo."
)


# Un 429 con un'attesa indicata da Google fino a questa soglia è un limite al
# minuto (richieste o token di input): si aspetta e si riprova. Oltre è la
# quota giornaliera (l'attesa indicata arriva all'azzeramento, ore): inutile
# aspettare dentro un giro.
MAX_RATE_LIMIT_WAIT_S = 300.0
# Margine sull'attesa indicata, per non riprovare un istante troppo presto.
_RATE_LIMIT_MARGIN_S = 1.0

_DURATION_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)s\s*$")
_RETRY_IN_RE = re.compile(r"retry in ([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE)


def _rate_limit_delay(exc: BaseException) -> float | None:
    """Secondi di attesa indicati da un 429: `RetryInfo.retryDelay` nei
    `details` dell'errore (es. `"46s"`), altrimenti il "Please retry in
    46.9s" del messaggio. `None` se non è un 429 o se nessun tempo è
    indicato."""
    if not isinstance(exc, errors.ClientError) or exc.code != 429:
        return None
    body = exc.details if isinstance(exc.details, dict) else {}
    error = body.get("error", body)
    for detail in error.get("details", []) or []:
        if isinstance(detail, dict) and detail.get("@type", "").endswith("google.rpc.RetryInfo"):
            match = _DURATION_RE.match(str(detail.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    match = _RETRY_IN_RE.search(str(error.get("message") or exc.message or ""))
    return float(match.group(1)) if match else None


def _is_retryable(exc: BaseException) -> bool:
    """Riprovabili: 5xx (`ServerError`, anche 504 di deadline), errori di
    rete/timeout del trasporto, e un 429 con un'attesa indicata entro
    `MAX_RATE_LIMIT_WAIT_S`. Non riprovabili: gli altri 4xx (`ClientError`)
    — la quota giornaliera esaurita, un modello inesistente, una richiesta
    malformata non cambiano riprovando — e qualunque altra eccezione, che è
    un errore del codice."""
    if isinstance(exc, (errors.ServerError, httpx.TransportError)):
        return True
    delay = _rate_limit_delay(exc)
    return delay is not None and delay <= MAX_RATE_LIMIT_WAIT_S


_backoff = wait_exponential(multiplier=2, min=2, max=20)


def _wait(retry_state: RetryCallState) -> float:
    """Su un 429 l'attesa indicata da Google (più un margine), altrimenti
    backoff esponenziale."""
    delay = _rate_limit_delay(retry_state.outcome.exception())
    if delay is not None:
        return delay + _RATE_LIMIT_MARGIN_S
    return _backoff(retry_state)


def _log_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception()
    logger.warning(
        "chiamata a Gemini fallita (tentativo %d: %s), nuovo tentativo tra %.1fs",
        retry_state.attempt_number, getattr(exc, "status", None) or type(exc).__name__,
        retry_state.upcoming_sleep,
    )


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=_wait,
    before_sleep=_log_retry,
    reraise=True,
)
def _call_gemini(
    client: genai.Client, *, model: str, prompt: str, response_schema: type[BaseModel]
) -> types.GenerateContentResponse:
    return client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=response_schema,
            http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT_MS),
        ),
    )


def _build_prompt(system_prompt: str, context: dict[str, Any]) -> str:
    context_json = json.dumps(context, default=str, indent=2, ensure_ascii=False)
    return f"{system_prompt}\n\nContesto:\n{context_json}"


_MARKDOWN_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)


def _strip_markdown_fence(text: str | None) -> str | None:
    """Toglie un eventuale code fence markdown (` ```json ... ``` `) attorno
    alla risposta. `response_mime_type="application/json"` non basta a
    impedirlo su tutti i modelli — i modelli Gemini propriamente detti lo
    rispettano, alcuni modelli Gemma osservati no (scoperto in un test
    end-to-end reale con `gemma-4-31b-it`)."""
    if not isinstance(text, str):
        return text
    match = _MARKDOWN_FENCE_RE.match(text.strip())
    return match.group(1) if match else text


class GeminiProvider:
    """Implementazione di `llm.base.LLMProvider` per Gemini."""

    def __init__(self, api_key: str | None = None, model: str = _DEFAULT_MODEL) -> None:
        self._client = genai.Client(api_key=api_key or get_api_key("GEMINI_API_KEY"))
        self._model = model

    def decide(self, context: dict[str, Any]) -> Decision:
        return self._generate(_DECISION_SYSTEM_PROMPT, context, Decision)

    def select_watchlist(self, context: dict[str, Any]) -> WatchlistSelection:
        return self._generate(_WATCHLIST_SYSTEM_PROMPT, context, WatchlistSelection)

    def _generate(
        self, system_prompt: str, context: dict[str, Any], schema: type[_SchemaT]
    ) -> _SchemaT:
        prompt = _build_prompt(system_prompt, context)

        try:
            response = _call_gemini(
                self._client, model=self._model, prompt=prompt, response_schema=schema
            )
        except Exception as exc:
            raise DecisionError(f"chiamata a Gemini fallita dopo i retry: {exc}") from exc

        try:
            return schema.model_validate_json(_strip_markdown_fence(response.text))
        except (ValidationError, ValueError, TypeError) as exc:
            raise DecisionError(
                f"risposta Gemini non valida rispetto a {schema.__name__}: {response.text!r}"
            ) from exc
