"""Test di integrazione per `db/backtest_reader.py` — richiedono Postgres
reale (il trigger di snapshot gira lato DB, non è simulabile in unit).

Usa la fixture `db_session`: il collegamento `run_id` via GUC di sessione
(`SET LOCAL`/`set_config`, `db/session.py`) funziona identico sulla
connessione della fixture — non serve `get_session()`/`track_model_run()`
per davvero, basta impostare la stessa GUC che leggerebbe il trigger prima
di scrivere la posizione.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from marketmind_ai.db.backtest_reader import get_position_events
from marketmind_ai.db.models.decisions import ModelRun
from marketmind_ai.db.models.market_data import Asset
from marketmind_ai.db.models.portfolio import Portfolio, PortfolioPosition

pytestmark = pytest.mark.integration

TEST_PORTFOLIO_ID = -1
TEST_ASSET_ID = -1


def _make_portfolio(session, portfolio_id: int = TEST_PORTFOLIO_ID) -> int:
    portfolio = Portfolio(
        portfolio_id=portfolio_id,
        name=f"test-backtest-reader-{portfolio_id}",
        portfolio_type="model",
        starting_capital=10_000.0,
        cash=10_000.0,
        equity_value=10_000.0,
        created_at=datetime.now(timezone.utc),
        is_active=True,
        llm_provider="gemini",
        model_version="test",
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


def _make_run(session, portfolio_id: int) -> int:
    # FK reale verso decisions.t_model_runs: la GUC da sola non basta, il
    # run deve esistere davvero (stesso vincolo che vale in produzione).
    run = ModelRun(
        portfolio_id=portfolio_id,
        ts=datetime.now(timezone.utc),
        config={},
        llm_provider="gemini",
        model_version="test",
    )
    session.add(run)
    session.flush()
    return run.run_id


def _set_model_run_guc(session, run_id: int | None) -> None:
    session.execute(
        text("SELECT set_config('marketmind.model_run_id', :value, true)"),
        {"value": "" if run_id is None else str(run_id)},
    )


class TestGetPositionEvents:
    def test_empty_when_no_trade_for_this_run(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        run_id = _make_run(db_session, portfolio_id)

        assert get_position_events(db_session, portfolio_id, run_id) == []

    def test_returns_event_linked_to_the_tracked_run(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        asset_id = _make_asset(db_session)
        run_id = _make_run(db_session, portfolio_id)
        _set_model_run_guc(db_session, run_id)

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

        events = get_position_events(db_session, portfolio_id, run_id)

        assert len(events) == 1
        assert events[0].asset_id == asset_id
        assert events[0].asset.symbol == "TESTX"
        assert events[0].quantity == 10.0
        assert events[0].avg_price == 100.0
        assert events[0].operation == "INSERT"
        assert events[0].run_id == run_id

    def test_excludes_events_from_other_runs(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        asset_id = _make_asset(db_session)
        run_id = _make_run(db_session, portfolio_id)
        other_run_id = _make_run(db_session, portfolio_id)
        _set_model_run_guc(db_session, other_run_id)

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

        assert get_position_events(db_session, portfolio_id, run_id) == []

    def test_excludes_events_not_linked_to_any_run(self, db_session):
        """Comportamento pre-fix, ancora valido: una scrittura fuori da
        track_model_run (nessuna GUC impostata) produce run_id NULL, non
        collegato a nessun run specifico."""
        portfolio_id = _make_portfolio(db_session)
        asset_id = _make_asset(db_session)
        run_id = _make_run(db_session, portfolio_id)

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

        assert get_position_events(db_session, portfolio_id, run_id) == []
