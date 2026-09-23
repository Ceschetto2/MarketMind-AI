"""Test di integrazione per `db/portfolio_reader.py` — richiedono Postgres reale.

Tutte le funzioni accettano una sessione iniettata: usano la fixture
`db_session` (rollback automatico a fine test).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketmind_ai.db.models.market_data import Asset
from marketmind_ai.db.models.portfolio import Portfolio, PortfolioPosition, PortfolioWatchlistEntry
from marketmind_ai.db.portfolio_reader import (
    get_active_model_portfolios,
    get_due_model_portfolios,
    get_portfolio,
    get_portfolio_positions,
    get_watchlist,
)

pytestmark = pytest.mark.integration

TEST_PORTFOLIO_ID = -1
TEST_PORTFOLIO_ID_2 = -2
TEST_ASSET_ID = -1


def _make_portfolio(session, **overrides) -> int:
    defaults = dict(
        portfolio_id=TEST_PORTFOLIO_ID,
        name="test-model-portfolio",
        portfolio_type="model",
        starting_capital=100_000.0,
        cash=80_000.0,
        equity_value=100_000.0,
        created_at=datetime.now(timezone.utc),
        is_active=True,
        llm_provider="gemini",
        model_version="gemini-3.6-flash",
        strategy_prompt="strategia di test",
    )
    defaults.update(overrides)
    portfolio = Portfolio(**defaults)
    session.add(portfolio)
    session.flush()
    return portfolio.portfolio_id


def _make_asset(session, asset_id: int = TEST_ASSET_ID, symbol: str = "TESTX") -> int:
    asset = Asset(
        asset_id=asset_id,
        symbol=symbol,
        name="Test Asset",
        sector="Test",
        asset_type="equity",
        source="yfinance",
        fetched_at=datetime.now(timezone.utc),
    )
    session.add(asset)
    session.flush()
    return asset.asset_id


class TestGetPortfolio:
    def test_returns_the_requested_portfolio(self, db_session):
        portfolio_id = _make_portfolio(db_session)

        portfolio = get_portfolio(db_session, portfolio_id)

        assert portfolio.portfolio_id == portfolio_id
        assert portfolio.cash == 80_000.0
        assert portfolio.llm_provider == "gemini"


class TestGetPortfolioPositions:
    def test_returns_only_positions_of_the_given_portfolio(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        other_portfolio_id = _make_portfolio(
            db_session, portfolio_id=TEST_PORTFOLIO_ID_2, name="other-portfolio"
        )
        asset_id = _make_asset(db_session)
        db_session.add_all(
            [
                PortfolioPosition(
                    portfolio_id=portfolio_id,
                    asset_id=asset_id,
                    quantity=10.0,
                    avg_price=150.0,
                    updated_at=datetime.now(timezone.utc),
                ),
                PortfolioPosition(
                    portfolio_id=other_portfolio_id,
                    asset_id=asset_id,
                    quantity=999.0,
                    avg_price=1.0,
                    updated_at=datetime.now(timezone.utc),
                ),
            ]
        )
        db_session.flush()

        positions = get_portfolio_positions(db_session, portfolio_id)

        assert len(positions) == 1
        assert positions[0].quantity == 10.0

    def test_no_positions_returns_empty_list(self, db_session):
        portfolio_id = _make_portfolio(db_session)

        assert get_portfolio_positions(db_session, portfolio_id) == []


class TestGetActiveModelPortfolios:
    def test_excludes_inactive_and_benchmark(self, db_session):
        active_model = _make_portfolio(db_session)
        inactive_model = _make_portfolio(
            db_session,
            portfolio_id=TEST_PORTFOLIO_ID_2,
            name="paused-portfolio",
            is_active=False,
        )
        benchmark = _make_portfolio(
            db_session,
            portfolio_id=-3,
            name="test-benchmark",
            portfolio_type="benchmark",
            is_active=True,
            llm_provider=None,
            model_version=None,
        )

        active = get_active_model_portfolios(db_session)
        ids = {p.portfolio_id for p in active if p.portfolio_id in (active_model, inactive_model, benchmark)}

        assert active_model in ids
        assert inactive_model not in ids
        assert benchmark not in ids


class TestGetDueModelPortfolios:
    def test_null_next_decision_at_is_due(self, db_session):
        """Mai schedulato prima (portfolio appena creato, non ancora
        inizializzato) — trattato come scaduto subito, non ignorato."""
        portfolio_id = _make_portfolio(db_session, next_decision_at=None)

        due = get_due_model_portfolios(db_session, datetime.now(timezone.utc))

        assert portfolio_id in {p.portfolio_id for p in due}

    def test_past_next_decision_at_is_due(self, db_session):
        as_of = datetime.now(timezone.utc)
        portfolio_id = _make_portfolio(
            db_session, next_decision_at=as_of - timedelta(days=1)
        )

        due = get_due_model_portfolios(db_session, as_of)

        assert portfolio_id in {p.portfolio_id for p in due}

    def test_future_next_decision_at_is_not_due(self, db_session):
        as_of = datetime.now(timezone.utc)
        portfolio_id = _make_portfolio(
            db_session, next_decision_at=as_of + timedelta(days=6)
        )

        due = get_due_model_portfolios(db_session, as_of)

        assert portfolio_id not in {p.portfolio_id for p in due}

    def test_excludes_inactive_and_benchmark_even_if_due(self, db_session):
        as_of = datetime.now(timezone.utc)
        inactive_model = _make_portfolio(
            db_session,
            portfolio_id=TEST_PORTFOLIO_ID_2,
            name="paused-portfolio",
            is_active=False,
            next_decision_at=None,
        )
        benchmark = _make_portfolio(
            db_session,
            portfolio_id=-3,
            name="test-benchmark",
            portfolio_type="benchmark",
            is_active=True,
            llm_provider=None,
            model_version=None,
            next_decision_at=None,
        )

        due_ids = {p.portfolio_id for p in get_due_model_portfolios(db_session, as_of)}

        assert inactive_model not in due_ids
        assert benchmark not in due_ids


class TestGetWatchlist:
    def test_returns_only_assets_of_the_given_portfolio(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        other_portfolio_id = _make_portfolio(
            db_session, portfolio_id=TEST_PORTFOLIO_ID_2, name="other-portfolio"
        )
        asset_id = _make_asset(db_session)
        other_asset_id = _make_asset(db_session, asset_id=-3, symbol="OTHERX")
        db_session.add_all(
            [
                PortfolioWatchlistEntry(
                    portfolio_id=portfolio_id,
                    asset_id=asset_id,
                    added_at=datetime.now(timezone.utc),
                ),
                PortfolioWatchlistEntry(
                    portfolio_id=other_portfolio_id,
                    asset_id=other_asset_id,
                    added_at=datetime.now(timezone.utc),
                ),
            ]
        )
        db_session.flush()

        watchlist = get_watchlist(db_session, portfolio_id)

        assert [a.symbol for a in watchlist] == ["TESTX"]

    def test_empty_watchlist_returns_empty_list(self, db_session):
        portfolio_id = _make_portfolio(db_session)

        assert get_watchlist(db_session, portfolio_id) == []
