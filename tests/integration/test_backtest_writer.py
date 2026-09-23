"""Test di integrazione per `db/backtest_writer.py` — richiede Postgres
reale (FK verso `decisions.t_model_runs`).

Usa la fixture `db_session` (rollback automatico a fine test).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from marketmind_ai.db.backtest_writer import write_backtest_result
from marketmind_ai.db.models.decisions import BacktestResult, ModelRun
from marketmind_ai.db.models.portfolio import Portfolio

pytestmark = pytest.mark.integration

TEST_PORTFOLIO_ID = -1


def _make_run(session) -> int:
    portfolio = Portfolio(
        portfolio_id=TEST_PORTFOLIO_ID,
        name="test-backtest-writer-portfolio",
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

    run = ModelRun(
        portfolio_id=portfolio.portfolio_id,
        ts=datetime.now(timezone.utc),
        config={},
        llm_provider="gemini",
        model_version="test",
    )
    session.add(run)
    session.flush()
    return run.run_id


class TestWriteBacktestResult:
    def test_creates_a_row_with_all_fields(self, db_session):
        run_id = _make_run(db_session)

        backtest_id = write_backtest_result(
            db_session,
            run_id=run_id,
            pnl=123.45,
            sharpe_ratio=1.2,
            max_drawdown=-0.1,
            win_rate=0.75,
            period_start=date(2026, 9, 14),
            period_end=date(2026, 9, 20),
        )

        row = db_session.get(BacktestResult, backtest_id)
        assert row.run_id == run_id
        assert row.pnl == 123.45
        assert row.sharpe_ratio == 1.2
        assert row.max_drawdown == -0.1
        assert row.win_rate == 0.75
        assert row.period_start == date(2026, 9, 14)
        assert row.period_end == date(2026, 9, 20)

    def test_nullable_metrics_can_be_none(self, db_session):
        run_id = _make_run(db_session)

        backtest_id = write_backtest_result(
            db_session,
            run_id=run_id,
            pnl=0.0,
            sharpe_ratio=None,
            max_drawdown=None,
            win_rate=None,
            period_start=date(2026, 9, 14),
            period_end=date(2026, 9, 14),
        )

        row = db_session.get(BacktestResult, backtest_id)
        assert row.sharpe_ratio is None
        assert row.max_drawdown is None
        assert row.win_rate is None

    def test_two_runs_produce_two_distinct_rows(self, db_session):
        run_id = _make_run(db_session)

        first_id = write_backtest_result(
            db_session,
            run_id=run_id,
            pnl=10.0,
            sharpe_ratio=None,
            max_drawdown=None,
            win_rate=None,
            period_start=date(2026, 9, 14),
            period_end=date(2026, 9, 14),
        )
        second_id = write_backtest_result(
            db_session,
            run_id=run_id,
            pnl=20.0,
            sharpe_ratio=None,
            max_drawdown=None,
            win_rate=None,
            period_start=date(2026, 9, 14),
            period_end=date(2026, 9, 15),
        )

        assert first_id != second_id
