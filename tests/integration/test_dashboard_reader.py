"""Test di integrazione per `db/dashboard_reader.py` — richiedono Postgres
reale (il trigger di snapshot gira lato DB).

Usa la fixture `db_session` (rollback automatico a fine test).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from marketmind_ai.db.dashboard_reader import (
    get_cash_history,
    get_decision_log,
    list_portfolios,
)
from marketmind_ai.db.models.decisions import ModelDecision, ModelRun
from marketmind_ai.db.models.market_data import Asset
from marketmind_ai.db.models.portfolio import Portfolio, PortfolioPosition
from marketmind_ai.llm.schemas import Decision

pytestmark = pytest.mark.integration

TEST_PORTFOLIO_ID = -1
TEST_ASSET_ID = -1


def _make_portfolio(
    session, portfolio_id: int = TEST_PORTFOLIO_ID, name: str = "test-dashboard-portfolio", **overrides
) -> int:
    defaults = dict(
        portfolio_id=portfolio_id,
        name=name,
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
    defaults.update(overrides)
    session.add(Portfolio(**defaults))
    session.flush()
    return portfolio_id


def _make_asset(session, asset_id: int = TEST_ASSET_ID, symbol: str = "TESTX") -> int:
    session.add(
        Asset(
            asset_id=asset_id,
            symbol=symbol,
            name="Test Asset",
            sector="Test",
            asset_type="equity",
            source="yfinance",
            fetched_at=datetime.now(timezone.utc),
        )
    )
    session.flush()
    return asset_id


def _make_run(session, portfolio_id: int) -> int:
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


class TestListPortfolios:
    def test_includes_model_and_benchmark_portfolios(self, db_session):
        _make_portfolio(db_session, portfolio_id=-1, name="test-model-portfolio")
        _make_portfolio(
            db_session,
            portfolio_id=-2,
            name="test-benchmark-portfolio",
            portfolio_type="benchmark",
            llm_provider=None,
            model_version=None,
            strategy_prompt=None,
        )

        names = {p.name for p in list_portfolios(db_session)}

        assert "test-model-portfolio" in names
        assert "test-benchmark-portfolio" in names

    def test_includes_inactive_portfolios(self, db_session):
        _make_portfolio(db_session, portfolio_id=-1, is_active=False)

        portfolios = {p.portfolio_id: p for p in list_portfolios(db_session)}

        assert -1 in portfolios
        assert portfolios[-1].is_active is False


class TestGetCashHistory:
    def test_creating_a_portfolio_already_produces_one_snapshot(self, db_session):
        """Il trigger scatta anche sull'INSERT, non solo sugli UPDATE
        successivi: un portfolio appena creato ha già un primo punto in
        curva (il capitale iniziale), non uno storico vuoto."""
        portfolio_id = _make_portfolio(db_session, cash=10_000.0)

        history = get_cash_history(db_session, portfolio_id)

        assert len(history) == 1
        assert history[0].cash == 10_000.0
        assert history[0].operation == "INSERT"

    def test_records_a_snapshot_per_cash_change(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=10_000.0)

        portfolio = db_session.get(Portfolio, portfolio_id)
        portfolio.cash = 8_000.0
        db_session.flush()

        history = get_cash_history(db_session, portfolio_id)

        assert [h.cash for h in history] == [10_000.0, 8_000.0]
        assert [h.operation for h in history] == ["INSERT", "UPDATE"]

    def test_ordered_chronologically(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=10_000.0)
        portfolio = db_session.get(Portfolio, portfolio_id)
        portfolio.cash = 9_000.0
        db_session.flush()
        portfolio.cash = 8_000.0
        db_session.flush()

        history = get_cash_history(db_session, portfolio_id)

        assert [h.cash for h in history] == [10_000.0, 9_000.0, 8_000.0]
        assert all(
            history[i].changed_at <= history[i + 1].changed_at
            for i in range(len(history) - 1)
        )


class TestGetDecisionLog:
    def test_empty_when_no_decisions(self, db_session):
        portfolio_id = _make_portfolio(db_session)

        assert get_decision_log(db_session, portfolio_id) == []

    def test_returns_decision_with_asset_loaded(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        asset_id = _make_asset(db_session)
        run_id = _make_run(db_session, portfolio_id)
        db_session.add(
            ModelDecision(
                run_id=run_id,
                asset_id=asset_id,
                ts=datetime.now(timezone.utc),
                decision="BUY",
                confidence=0.8,
                reasoning="trend positivo",
                size_pct=0.2,
                context_snapshot={},
            )
        )
        db_session.flush()

        rows = get_decision_log(db_session, portfolio_id)

        assert len(rows) == 1
        assert rows[0].decision == "BUY"
        assert rows[0].asset.symbol == "TESTX"

    def test_excludes_decisions_of_other_portfolios(self, db_session):
        portfolio_id = _make_portfolio(db_session, portfolio_id=-1, name="p1")
        other_portfolio_id = _make_portfolio(db_session, portfolio_id=-2, name="p2")
        asset_id = _make_asset(db_session)
        other_run_id = _make_run(db_session, other_portfolio_id)
        db_session.add(
            ModelDecision(
                run_id=other_run_id,
                asset_id=asset_id,
                ts=datetime.now(timezone.utc),
                decision="HOLD",
                context_snapshot={},
            )
        )
        db_session.flush()

        assert get_decision_log(db_session, portfolio_id) == []

    def test_most_recent_first_and_respects_limit(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        asset_id = _make_asset(db_session)
        run_id = _make_run(db_session, portfolio_id)
        base_ts = datetime(2026, 9, 1, tzinfo=timezone.utc)
        for i in range(3):
            db_session.add(
                ModelDecision(
                    run_id=run_id,
                    asset_id=asset_id,
                    ts=base_ts.replace(day=1 + i),
                    decision="HOLD",
                    context_snapshot={"i": i},
                )
            )
        db_session.flush()

        rows = get_decision_log(db_session, portfolio_id, limit=2)

        assert len(rows) == 2
        assert rows[0].ts > rows[1].ts
        assert rows[0].context_snapshot == {"i": 2}
