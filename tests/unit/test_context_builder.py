"""Test unitari per `decision_engine/context_builder.py`.

Nessun accesso a DB: le funzioni di `db/context_reader.py` e
`db/portfolio_reader.py` sono mockate con `pytest-mock`. Lo scopo è la
composizione (quali finestre passa, come converte le righe ORM in snippet
Pydantic, come isola lo stato del portfolio), non le query in sé (già
coperte dai rispettivi test di integrazione).
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
PORTFOLIO_ID = 7


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


def _mock_portfolio(mocker, portfolio_id=PORTFOLIO_ID, name="test-portfolio", cash=10_000.0):
    portfolio = mocker.Mock()
    portfolio.portfolio_id = portfolio_id
    portfolio.name = name
    portfolio.cash = cash
    return portfolio


def _mock_position(mocker, symbol, quantity, avg_price):
    position = mocker.Mock()
    position.asset = mocker.Mock(symbol=symbol)
    position.quantity = quantity
    position.avg_price = avg_price
    return position


def _patch_empty_market_data(mocker):
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
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_portfolio",
            return_value=_mock_portfolio(mocker),
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_portfolio_positions",
            return_value=[],
        )
        session = mocker.Mock()

        build_context(session, asset_id=42, symbol="AAPL", portfolio_id=PORTFOLIO_ID, as_of=AS_OF)

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
        _patch_empty_market_data(mocker)
        mock_prices = mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_recent_prices", return_value=[]
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_portfolio",
            return_value=_mock_portfolio(mocker),
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_portfolio_positions",
            return_value=[],
        )
        session = mocker.Mock()

        build_context(
            session,
            asset_id=42,
            symbol="AAPL",
            portfolio_id=PORTFOLIO_ID,
            as_of=AS_OF,
            price_days_back=60,
        )

        mock_prices.assert_called_once_with(session, 42, as_of=AS_OF, days_back=60)

    def test_defaults_as_of_to_now_when_omitted(self, mocker):
        _patch_empty_market_data(mocker)
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_portfolio",
            return_value=_mock_portfolio(mocker),
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_portfolio_positions",
            return_value=[],
        )
        session = mocker.Mock()

        context = build_context(session, asset_id=42, symbol="AAPL", portfolio_id=PORTFOLIO_ID)

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
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_portfolio",
            return_value=_mock_portfolio(mocker, cash=25_000.0),
        )
        mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_portfolio_positions",
            return_value=[_mock_position(mocker, "MSFT", 5.0, 300.0)],
        )
        session = mocker.Mock()

        context = build_context(session, asset_id=42, symbol="AAPL", portfolio_id=PORTFOLIO_ID, as_of=AS_OF)

        assert context.asset_id == 42
        assert context.symbol == "AAPL"
        assert context.prices[0].close == 150.0
        assert context.news[0].headline == "titolo"
        assert context.macro_events[0].indicator == "UNRATE"
        assert context.company_events[0].event_type == "earnings"
        assert context.portfolio.portfolio_id == PORTFOLIO_ID
        assert context.portfolio.cash == 25_000.0
        assert context.portfolio.positions[0].symbol == "MSFT"
        assert context.portfolio.positions[0].quantity == 5.0

    def test_only_reads_state_of_the_given_portfolio(self, mocker):
        """L'isolamento richiesto dal disegno: build_context non deve mai
        interrogare più portfolio insieme."""
        _patch_empty_market_data(mocker)
        mock_get_portfolio = mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_portfolio",
            return_value=_mock_portfolio(mocker),
        )
        mock_get_positions = mocker.patch(
            "marketmind_ai.decision_engine.context_builder.get_portfolio_positions",
            return_value=[],
        )
        session = mocker.Mock()

        build_context(session, asset_id=42, symbol="AAPL", portfolio_id=PORTFOLIO_ID, as_of=AS_OF)

        mock_get_portfolio.assert_called_once_with(session, PORTFOLIO_ID)
        mock_get_positions.assert_called_once_with(session, PORTFOLIO_ID)
