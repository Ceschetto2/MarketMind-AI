"""Test unitari per `decision_engine/context_builder.py`.

Nessun accesso a DB: le funzioni di `db/context_reader.py` sono mockate con
`pytest-mock`. Lo scopo è la composizione (quali finestre passa, come
converte le righe ORM in snippet Pydantic), non le query in sé (già coperte
da `tests/integration/test_context_reader.py`).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from marketmind_ai.decision_engine.context_builder import (
    DEFAULT_COMPANY_EVENT_DAYS_BACK,
    DEFAULT_NEWS_DAYS_BACK,
    DEFAULT_NEWS_MAX_ITEMS,
    DEFAULT_PRICE_DAYS_BACK,
    build_context,
)

AS_OF = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def _mock_price(mocker, ts, close):
    row = mocker.Mock()
    row.ts = ts
    row.close = close
    return row


def _mock_news(mocker, ts, headline, sentiment_score=None):
    row = mocker.Mock()
    row.ts = ts
    row.headline = headline
    row.sentiment_score = sentiment_score
    return row


def _mock_macro(mocker, indicator, ts, value):
    row = mocker.Mock()
    row.indicator = indicator
    row.ts = ts
    row.value = value
    return row


def _mock_company_event(mocker, ts, event_type):
    row = mocker.Mock()
    row.ts = ts
    row.event_type = event_type
    return row


class TestBuildContext:
    def test_uses_default_windows_when_not_overridden(self, mocker):
        mock_prices = mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_recent_prices", return_value=[]
        )
        mock_news = mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_recent_news", return_value=[]
        )
        mock_macro = mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_latest_macro_events",
            return_value=[],
        )
        mock_events = mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_recent_company_events",
            return_value=[],
        )
        session = mocker.Mock()

        build_context(session, asset_id=42, symbol="AAPL", as_of=AS_OF)

        mock_prices.assert_called_once_with(
            session, 42, as_of=AS_OF, days_back=DEFAULT_PRICE_DAYS_BACK
        )
        mock_news.assert_called_once_with(
            session,
            42,
            as_of=AS_OF,
            days_back=DEFAULT_NEWS_DAYS_BACK,
            max_items=DEFAULT_NEWS_MAX_ITEMS,
        )
        mock_macro.assert_called_once_with(session, as_of=AS_OF)
        mock_events.assert_called_once_with(
            session, 42, as_of=AS_OF, days_back=DEFAULT_COMPANY_EVENT_DAYS_BACK
        )

    def test_accepts_window_overrides(self, mocker):
        mock_prices = mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_recent_prices", return_value=[]
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_recent_news", return_value=[]
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_latest_macro_events",
            return_value=[],
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_recent_company_events",
            return_value=[],
        )
        session = mocker.Mock()

        build_context(session, asset_id=42, symbol="AAPL", as_of=AS_OF, price_days_back=60)

        mock_prices.assert_called_once_with(session, 42, as_of=AS_OF, days_back=60)

    def test_defaults_as_of_to_now_when_omitted(self, mocker):
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_recent_prices", return_value=[]
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_recent_news", return_value=[]
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_latest_macro_events",
            return_value=[],
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_recent_company_events",
            return_value=[],
        )
        session = mocker.Mock()

        context = build_context(session, asset_id=42, symbol="AAPL")

        assert context.as_of.tzinfo is not None

    def test_converts_orm_rows_into_typed_snippets(self, mocker):
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_recent_prices",
            return_value=[_mock_price(mocker, AS_OF, 150.0)],
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_recent_news",
            return_value=[_mock_news(mocker, AS_OF, "titolo", 0.2)],
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_latest_macro_events",
            return_value=[_mock_macro(mocker, "UNRATE", date(2026, 8, 1), 4.1)],
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_recent_company_events",
            return_value=[_mock_company_event(mocker, date(2026, 8, 15), "earnings")],
        )
        session = mocker.Mock()

        context = build_context(session, asset_id=42, symbol="AAPL", as_of=AS_OF)

        assert context.asset_id == 42
        assert context.symbol == "AAPL"
        assert context.prices[0].close == 150.0
        assert context.news[0].headline == "titolo"
        assert context.macro_events[0].indicator == "UNRATE"
        assert context.company_events[0].event_type == "earnings"
