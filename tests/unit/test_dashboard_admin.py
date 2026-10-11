"""Test unitari della validazione di un nuovo portfolio (`NewModelPortfolio`)."""

from __future__ import annotations

import pytest

from marketmind_frontend.admin import NewModelPortfolio


def _spec(**overrides) -> NewModelPortfolio:
    values = dict(name=" growth ", starting_capital=1_000.0, llm_provider="gemini",
                  model_version="gemini-x", strategy_prompt=" tech ")
    values.update(overrides)
    return NewModelPortfolio(**values)


def test_text_fields_are_trimmed():
    spec = _spec()

    assert (spec.name, spec.strategy_prompt) == ("growth", "tech")


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"name": "  "}, "nome"),
        ({"model_version": ""}, "modello"),
        ({"strategy_prompt": "\n"}, "strategia"),
        ({"starting_capital": 0.0}, "capitale"),
        ({"llm_provider": "openai"}, "provider"),
    ],
)
def test_invalid_values_rejected(overrides, message):
    with pytest.raises(ValueError, match=message):
        _spec(**overrides)
