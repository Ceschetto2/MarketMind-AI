"""Test unitari per `llm/factory.py`.

Nessun accesso a rete: `GeminiProvider.__init__` chiama `genai.Client`, che
qui riceve una `api_key` finta — non fa alcuna chiamata di rete alla
costruzione.
"""

from __future__ import annotations

import pytest

from marketmind_ai.llm.factory import get_provider
from marketmind_ai.llm.gemini import GeminiProvider


class TestGetProvider:
    def test_returns_gemini_provider_for_gemini(self):
        provider = get_provider("gemini", model="gemini-3.6-flash", api_key="fake-key")

        assert isinstance(provider, GeminiProvider)

    def test_gemma_models_are_served_by_the_same_provider(self):
        provider = get_provider("gemini", model="gemma-4-31b-it", api_key="fake-key")

        assert isinstance(provider, GeminiProvider)

    def test_raises_on_unknown_provider(self):
        with pytest.raises(ValueError, match="sconosciuto"):
            get_provider("openai", model="gpt-x", api_key="fake-key")
