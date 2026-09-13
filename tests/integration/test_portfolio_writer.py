"""Test di integrazione per `db/portfolio_writer.py` — richiedono Postgres reale.

Usa la fixture `db_session` (rollback automatico a fine test).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from marketmind_ai.db.models.market_data import Asset
from marketmind_ai.db.models.portfolio import Portfolio, PortfolioWatchlistEntry
from marketmind_ai.db.portfolio_writer import write_watchlist

pytestmark = pytest.mark.integration

TEST_PORTFOLIO_ID = -1
AS_OF = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def _make_portfolio(session) -> int:
    portfolio = Portfolio(
        portfolio_id=TEST_PORTFOLIO_ID,
        name="test-portfolio",
        portfolio_type="model",
        starting_capital=100_000.0,
        cash=100_000.0,
        equity_value=100_000.0,
        created_at=datetime.now(timezone.utc),
        is_active=True,
        llm_provider="gemini",
        model_version="gemini-3.6-flash",
        strategy_prompt="strategia di test",
    )
    session.add(portfolio)
    session.flush()
    return portfolio.portfolio_id


def _make_asset(session, asset_id: int, symbol: str) -> int:
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


class TestWriteWatchlist:
    def test_creates_one_row_per_asset(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        asset_id_1 = _make_asset(db_session, -1, "AAA")
        asset_id_2 = _make_asset(db_session, -2, "BBB")

        write_watchlist(db_session, portfolio_id, [asset_id_1, asset_id_2], added_at=AS_OF)

        rows = db_session.execute(
            select(PortfolioWatchlistEntry).where(
                PortfolioWatchlistEntry.portfolio_id == portfolio_id
            )
        ).scalars().all()
        assert {r.asset_id for r in rows} == {asset_id_1, asset_id_2}

    def test_replaces_existing_watchlist_instead_of_appending(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        asset_id_1 = _make_asset(db_session, -1, "AAA")
        asset_id_2 = _make_asset(db_session, -2, "BBB")
        write_watchlist(db_session, portfolio_id, [asset_id_1], added_at=AS_OF)

        write_watchlist(db_session, portfolio_id, [asset_id_2], added_at=AS_OF)

        rows = db_session.execute(
            select(PortfolioWatchlistEntry).where(
                PortfolioWatchlistEntry.portfolio_id == portfolio_id
            )
        ).scalars().all()
        assert {r.asset_id for r in rows} == {asset_id_2}

    def test_empty_selection_clears_the_watchlist(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        asset_id_1 = _make_asset(db_session, -1, "AAA")
        write_watchlist(db_session, portfolio_id, [asset_id_1], added_at=AS_OF)

        write_watchlist(db_session, portfolio_id, [], added_at=AS_OF)

        rows = db_session.execute(
            select(PortfolioWatchlistEntry).where(
                PortfolioWatchlistEntry.portfolio_id == portfolio_id
            )
        ).scalars().all()
        assert rows == []
