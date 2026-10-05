"""Test unitari dell'entry point del Decision Engine
(`python -m marketmind_llm_decision_engine`)."""

from __future__ import annotations

import signal
from datetime import datetime, timezone

import pytest

from marketmind_db.access import APP
from marketmind_llm_decision_engine import __main__ as cli
from marketmind_llm_decision_engine.backtest.engine import BacktestMetrics, NoTradesForRunError
from marketmind_llm_decision_engine.decision_engine.engine import PortfolioRunResult
from marketmind_llm_decision_engine.llm.exceptions import DecisionError


def _result(status: str) -> PortfolioRunResult:
    return PortfolioRunResult(portfolio_id=1, run_id=1, status=status, decisions_total=1)


@pytest.fixture(autouse=True)
def no_logging_setup(mocker):
    mocker.patch.object(cli, "configure_logging")


@pytest.fixture
def database(mocker):
    return mocker.patch.object(cli, "Database")


@pytest.fixture
def engine(mocker):
    return mocker.patch.object(cli, "DecisionEngine").return_value


class TestRunDue:
    def test_database_con_policy_app(self, database, engine):
        engine.run_due.return_value = []

        assert cli.main(["run-due"]) == 0

        assert database.call_args.kwargs["policy"] is APP
        assert database.call_args.kwargs["dry_run"] is False

    def test_dry_run(self, database, engine):
        engine.run_due.return_value = []

        cli.main(["run-due", "--dry-run"])

        assert database.call_args.kwargs["dry_run"] is True

    @pytest.mark.parametrize(
        ("statuses", "code"),
        [([], 0), (["success", "partial"], 0), (["success", "failed"], 1)],
    )
    def test_exit_code(self, database, engine, statuses, code):
        engine.run_due.return_value = [_result(s) for s in statuses]

        assert cli.main(["run-due"]) == code

    def test_sigterm_chiude_con_143(self, database, engine):
        def interrupted(*args, **kwargs):
            signal.raise_signal(signal.SIGTERM)

        engine.run_due.side_effect = interrupted
        previous = signal.getsignal(signal.SIGTERM)

        assert cli.main(["run-due"]) == 128 + signal.SIGTERM
        assert signal.getsignal(signal.SIGTERM) == previous


class TestInitPortfolio:
    @pytest.mark.parametrize(("status", "code"), [("success", 0), ("partial", 0), ("failed", 1)])
    def test_exit_code(self, database, engine, status, code):
        engine.initialize_portfolio.return_value = _result(status)

        assert cli.main(["init-portfolio", "7"]) == code
        engine.initialize_portfolio.assert_called_once_with(7)

    def test_bootstrap_fallito(self, database, engine):
        engine.initialize_portfolio.side_effect = DecisionError("quota")

        assert cli.main(["init-portfolio", "7"]) == 1


class TestBacktest:
    def test_backtest_salvato(self, mocker, database):
        backtester = mocker.patch.object(cli, "Backtester").return_value
        metrics = BacktestMetrics(1.0, None, None, None, datetime(2026, 9, 1).date(), datetime(2026, 9, 2).date())
        backtester.run.return_value = (metrics, 5)

        assert cli.main(["backtest", "4", "41"]) == 0

        kwargs = backtester.run.call_args.kwargs
        assert (kwargs["portfolio_id"], kwargs["run_id"], kwargs["save"]) == (4, 41, True)
        assert kwargs["as_of"].tzinfo is timezone.utc

    def test_no_save(self, mocker, database):
        backtester = mocker.patch.object(cli, "Backtester").return_value
        backtester.run.return_value = (mocker.Mock(), None)

        cli.main(["backtest", "4", "41", "--no-save"])

        assert backtester.run.call_args.kwargs["save"] is False

    def test_run_senza_trade(self, mocker, database):
        mocker.patch.object(cli, "Backtester").return_value.run.side_effect = NoTradesForRunError("nessun trade")

        assert cli.main(["backtest", "4", "41"]) == 1
