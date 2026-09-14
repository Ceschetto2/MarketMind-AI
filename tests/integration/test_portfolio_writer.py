"""Test di integrazione per `db/portfolio_writer.py` — richiedono Postgres reale.

Usa la fixture `db_session` (rollback automatico a fine test).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from marketmind_ai.db.models.market_data import Asset
from marketmind_ai.db.models.portfolio import Portfolio, PortfolioPosition, PortfolioWatchlistEntry
from marketmind_ai.db.portfolio_writer import execute_trade, write_watchlist
from marketmind_ai.llm.schemas import Decision

pytestmark = pytest.mark.integration

TEST_PORTFOLIO_ID = -1
TEST_ASSET_ID = -1
AS_OF = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def _make_portfolio(session, cash: float = 100_000.0) -> int:
    portfolio = Portfolio(
        portfolio_id=TEST_PORTFOLIO_ID,
        name="test-portfolio",
        portfolio_type="model",
        starting_capital=100_000.0,
        cash=cash,
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


def _get_portfolio(session, portfolio_id: int) -> Portfolio:
    return session.execute(
        select(Portfolio).where(Portfolio.portfolio_id == portfolio_id)
    ).scalar_one()


def _get_position(session, portfolio_id: int, asset_id: int) -> PortfolioPosition | None:
    return session.execute(
        select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id,
            PortfolioPosition.asset_id == asset_id,
        )
    ).scalar_one_or_none()


class TestExecuteTradeBuy:
    def test_buy_creates_a_new_position_and_debits_cash(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=10_000.0)
        asset_id = _make_asset(db_session)
        decision = Decision(decision="BUY", size_pct=0.5)

        execute_trade(db_session, portfolio_id, asset_id, decision, price=100.0)

        portfolio = _get_portfolio(db_session, portfolio_id)
        position = _get_position(db_session, portfolio_id, asset_id)
        assert portfolio.cash == 5_000.0
        assert position.quantity == 50.0
        assert position.avg_price == 100.0

    def test_buy_on_existing_position_recomputes_weighted_avg_price(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=10_000.0)
        asset_id = _make_asset(db_session)
        db_session.add(
            PortfolioPosition(
                portfolio_id=portfolio_id,
                asset_id=asset_id,
                quantity=10.0,
                avg_price=100.0,
                updated_at=datetime.now(timezone.utc),
            )
        )
        db_session.flush()

        # +1000 cash di acquisto a 200/azione -> 5 nuove azioni
        execute_trade(db_session, portfolio_id, asset_id, Decision(decision="BUY", size_pct=0.1), price=200.0)

        position = _get_position(db_session, portfolio_id, asset_id)
        # (10*100 + 5*200) / 15 = 133.33...
        assert position.quantity == 15.0
        assert position.avg_price == pytest.approx(133.333, rel=1e-3)

    def test_zero_size_pct_is_a_noop(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=10_000.0)
        asset_id = _make_asset(db_session)

        execute_trade(db_session, portfolio_id, asset_id, Decision(decision="BUY", size_pct=0.0), price=100.0)

        portfolio = _get_portfolio(db_session, portfolio_id)
        assert portfolio.cash == 10_000.0
        assert _get_position(db_session, portfolio_id, asset_id) is None


class TestExecuteTradeSell:
    def test_partial_sell_reduces_quantity_and_credits_cash(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=1_000.0)
        asset_id = _make_asset(db_session)
        db_session.add(
            PortfolioPosition(
                portfolio_id=portfolio_id,
                asset_id=asset_id,
                quantity=10.0,
                avg_price=100.0,
                updated_at=datetime.now(timezone.utc),
            )
        )
        db_session.flush()

        execute_trade(db_session, portfolio_id, asset_id, Decision(decision="SELL", size_pct=0.5), price=120.0)

        portfolio = _get_portfolio(db_session, portfolio_id)
        position = _get_position(db_session, portfolio_id, asset_id)
        assert portfolio.cash == 1_000.0 + 5 * 120.0
        assert position.quantity == 5.0
        assert position.avg_price == 100.0

    def test_full_sell_removes_the_position(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=0.0)
        asset_id = _make_asset(db_session)
        db_session.add(
            PortfolioPosition(
                portfolio_id=portfolio_id,
                asset_id=asset_id,
                quantity=10.0,
                avg_price=100.0,
                updated_at=datetime.now(timezone.utc),
            )
        )
        db_session.flush()

        execute_trade(db_session, portfolio_id, asset_id, Decision(decision="SELL", size_pct=1.0), price=120.0)

        portfolio = _get_portfolio(db_session, portfolio_id)
        assert portfolio.cash == 1_200.0
        assert _get_position(db_session, portfolio_id, asset_id) is None

    def test_sell_with_no_position_is_a_noop(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=1_000.0)
        asset_id = _make_asset(db_session)

        execute_trade(db_session, portfolio_id, asset_id, Decision(decision="SELL", size_pct=1.0), price=120.0)

        portfolio = _get_portfolio(db_session, portfolio_id)
        assert portfolio.cash == 1_000.0
        assert _get_position(db_session, portfolio_id, asset_id) is None


class TestExecuteTradeHold:
    def test_hold_raises_value_error(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        asset_id = _make_asset(db_session)

        with pytest.raises(ValueError):
            execute_trade(db_session, portfolio_id, asset_id, Decision(decision="HOLD"), price=100.0)


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
