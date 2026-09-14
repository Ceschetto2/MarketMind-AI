"""Costruisce un `LLMProvider` a partire da un nome, non da un import diretto.

Serve perché ora è il portfolio (`portfolio.t_portfolios.llm_provider`/
`model_version`) a decidere quale provider usare per il proprio run, non più
il chiamante che istanzia direttamente `GeminiProvider` — `decision_engine/`
deve continuare a dipendere solo dall'interfaccia generica di `llm/`, mai da
un'implementazione concreta (`llm/__init__.py`).
"""

from __future__ import annotations

from marketmind_ai.llm.base import LLMProvider
from marketmind_ai.llm.gemini import GeminiProvider

# "gemini" copre entrambe le famiglie di modelli servite dallo stesso
# client google-genai (Gemini e Gemma, verificato in un test end-to-end
# reale con gemma-4-31b-it) — non "gemini"/"gemma" distinti, dato che è lo
# stesso identico provider/SDK a servirli.
_PROVIDERS: dict[str, type[GeminiProvider]] = {
    "gemini": GeminiProvider,
}


def get_provider(name: str, model: str, api_key: str | None = None) -> LLMProvider:
    try:
        provider_cls = _PROVIDERS[name]
    except KeyError:
        raise ValueError(
            f"provider LLM sconosciuto: {name!r} (disponibili: {sorted(_PROVIDERS)})"
        ) from None
    return provider_cls(api_key=api_key, model=model)
