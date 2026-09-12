"""Test unitari per `decision_engine/schemas.py`.

Nessun accesso a rete/DB: classi Pydantic pure.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from marketmind_ai.decision_engine.schemas import (
    CompanyEventSnippet,
    DecisionContext,
    MacroSnippet,
    NewsSnippet,
    PricePoint,
)

AS_OF = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


class TestDecisionContext:
    def test_valid_construction_with_all_categories(self):
        context = DecisionContext(
            asset_id=42,
            symbol="AAPL",
            as_of=AS_OF,
            prices=[PricePoint(ts=AS_OF, close=150.0)],
            news=[NewsSnippet(ts=AS_OF, headline="Apple annuncia...", sentiment_score=0.3)],
            macro_events=[MacroSnippet(indicator="UNRATE", ts=date(2026, 8, 1), value=4.1)],
            company_events=[CompanyEventSnippet(ts=date(2026, 8, 15), event_type="earnings")],
        )

        assert context.asset_id == 42
        assert context.symbol == "AAPL"
        assert len(context.prices) == 1
        assert len(context.news) == 1
        assert len(context.macro_events) == 1
        assert len(context.company_events) == 1

    def test_empty_categories_are_valid(self):
        context = DecisionContext(
            asset_id=42,
            symbol="AAPL",
            as_of=AS_OF,
            prices=[],
            news=[],
            macro_events=[],
            company_events=[],
        )

        assert context.prices == []
        assert context.news == []

    def test_model_dump_is_json_serializable(self):
        context = DecisionContext(
            asset_id=42,
            symbol="AAPL",
            as_of=AS_OF,
            prices=[PricePoint(ts=AS_OF, close=150.0)],
            news=[],
            macro_events=[],
            company_events=[],
        )

        dumped = context.model_dump(mode="json")

        assert dumped["symbol"] == "AAPL"
        assert isinstance(dumped["as_of"], str)
        assert isinstance(dumped["prices"][0]["ts"], str)


class TestNewsSnippet:
    def test_sentiment_score_defaults_to_none(self):
        snippet = NewsSnippet(ts=AS_OF, headline="titolo")

        assert snippet.sentiment_score is None


class TestMacroSnippet:
    def test_value_defaults_to_none(self):
        snippet = MacroSnippet(indicator="UNRATE", ts=date(2026, 8, 1))

        assert snippet.value is None
