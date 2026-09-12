"""Provider LLM per Gemini, dietro l'interfaccia generica di `base.py`.

SDK nativo `google-genai`, senza tool-use/function-calling: la prima
decisione BUY/SELL/HOLD non ne ha bisogno, il function-calling per il
retrieval agentic (§2.4 di `Market Mind AI.md`) è rimandato a quando
servirà davvero. `response_schema=Decision` chiede a Gemini output
strutturato lato API, ma il parsing/validazione Pydantic locale su
`response.text` resta comunque il controllo finale — non ci si fida
ciecamente del rispetto dello schema lato provider.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from marketmind_ai.config import get_api_key
from marketmind_ai.llm.exceptions import DecisionError
from marketmind_ai.llm.schemas import Decision

logger = logging.getLogger(__name__)

# gemini-2.5-flash non è più disponibile ai nuovi utenti (404 dall'API,
# scoperto in un test end-to-end reale, non dai test a priori — mockano il
# client, non convalidano il nome modello contro l'API vera).
_DEFAULT_MODEL = "gemini-3.6-flash"

_SYSTEM_PROMPT = (
    "Sei il motore decisionale di una piattaforma di simulazione finanziaria. "
    "In base esclusivamente al contesto fornito qui sotto — nessuna "
    "informazione oltre il timestamp di decisione in esso contenuto — "
    "decidi se BUY, SELL o HOLD per l'asset descritto. Rispondi solo con "
    "l'oggetto JSON richiesto dallo schema, nessun testo aggiuntivo."
)


@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    reraise=True,
)
def _call_gemini(client: genai.Client, *, model: str, prompt: str) -> types.GenerateContentResponse:
    return client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Decision,
        ),
    )


def _build_prompt(context: dict[str, Any]) -> str:
    context_json = json.dumps(context, default=str, indent=2, ensure_ascii=False)
    return f"{_SYSTEM_PROMPT}\n\nContesto:\n{context_json}"


class GeminiProvider:
    """Implementazione di `llm.base.LLMProvider` per Gemini."""

    def __init__(self, api_key: str | None = None, model: str = _DEFAULT_MODEL) -> None:
        self._client = genai.Client(api_key=api_key or get_api_key("GEMINI_API_KEY"))
        self._model = model

    def decide(self, context: dict[str, Any]) -> Decision:
        prompt = _build_prompt(context)

        try:
            response = _call_gemini(self._client, model=self._model, prompt=prompt)
        except Exception as exc:
            raise DecisionError(
                f"chiamata a Gemini fallita dopo i retry: {exc}"
            ) from exc

        try:
            return Decision.model_validate_json(response.text)
        except (ValidationError, ValueError, TypeError) as exc:
            raise DecisionError(
                f"risposta Gemini non valida rispetto a Decision: {response.text!r}"
            ) from exc
