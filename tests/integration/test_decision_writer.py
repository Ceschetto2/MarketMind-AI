"""Test di integrazione per `db/decision_writer.py` — richiedono Postgres reale.

Entrambe le funzioni accettano una sessione iniettata: usano la fixture
`db_session` (rollback automatico a fine test).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from marketmind_ai.db.decision_writer import create_model_run, write_model_decision
from marketmind_ai.db.models.decisions import ModelDecision, ModelRun
from marketmind_ai.db.models.market_data import Asset
from marketmind_ai.db.models.portfolio import Portfolio
from marketmind_ai.llm.schemas import Decision

pytestmark = pytest.mark.integration

TEST_ASSET_ID = -1
TEST_PORTFOLIO_ID = -1
AS_OF = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def _make_asset(session) -> int:
    asset = Asset(
        asset_id=TEST_ASSET_ID,
        symbol="TESTX",
        name="Test Asset",
        sector="Test",
        asset_type="equity",
        source="yfinance",
        fetched_at=datetime.now(timezone.utc),
    )
    session.add(asset)
    session.flush()
    return asset.asset_id


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


class TestCreateModelRun:
    def test_creates_row_and_returns_run_id(self, db_session):
        portfolio_id = _make_portfolio(db_session)

        run_id = create_model_run(
            db_session,
            portfolio_id=portfolio_id,
            ts=AS_OF,
            config={"price_days_back": 30},
            llm_provider="gemini",
            model_version="gemini-3.6-flash",
        )

        row = db_session.execute(
            select(ModelRun).where(ModelRun.run_id == run_id)
        ).scalar_one()
        assert row.portfolio_id == portfolio_id
        assert row.llm_provider == "gemini"
        assert row.model_version == "gemini-3.6-flash"
        assert row.config == {"price_days_back": 30}


class TestWriteModelDecision:
    def test_creates_row_linked_to_run_and_asset(self, db_session):
        asset_id = _make_asset(db_session)
        portfolio_id = _make_portfolio(db_session)
        run_id = create_model_run(
            db_session,
            portfolio_id=portfolio_id,
            ts=AS_OF,
            config={},
            llm_provider="gemini",
            model_version="v1",
        )
        decision = Decision(decision="BUY", confidence=0.7, reasoning="momentum positivo")

        decision_id = write_model_decision(
            db_session,
            run_id=run_id,
            asset_id=asset_id,
            ts=AS_OF,
            decision=decision,
            context_snapshot={"symbol": "TESTX"},
        )

        row = db_session.execute(
            select(ModelDecision).where(ModelDecision.decision_id == decision_id)
        ).scalar_one()
        assert row.run_id == run_id
        assert row.asset_id == asset_id
        assert row.decision == "BUY"
        assert row.confidence == 0.7
        assert row.reasoning == "momentum positivo"
        assert row.context_snapshot == {"symbol": "TESTX"}
