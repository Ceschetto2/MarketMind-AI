"""Interfaccia generica di provider LLM e implementazioni concrete.

`decision_engine/` dipende solo da `LLMProvider`/`Decision`/`DecisionError`,
mai da un'implementazione specifica come `GeminiProvider`.
"""

from marketmind_llm_decision_engine.llm.base import LLMProvider
from marketmind_llm_decision_engine.llm.exceptions import DecisionError
from marketmind_llm_decision_engine.llm.factory import get_provider
from marketmind_llm_decision_engine.llm.gemini import GeminiProvider
from marketmind_llm_decision_engine.llm.schemas import DeferralRequest, Decision, WatchlistSelection

__all__ = [
    "DeferralRequest",
    "Decision",
    "DecisionError",
    "GeminiProvider",
    "LLMProvider",
    "WatchlistSelection",
    "get_provider",
]
