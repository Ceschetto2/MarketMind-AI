"""Test unitari per `decision_engine/engine.py`.

Nessun accesso a rete/DB: sessione, lettura di watchlist/portfolio, context
builder, provider e scritture sono tutte mockate con `pytest-mock`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from marketmind_ai.decision_engine.engine import initialize_portfolio, run_weekly_decisions
from marketmind_ai.llm.exceptions import DecisionError
from marketmind_ai.llm.schemas import Decision, WatchlistSelection

AS_OF = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def _mock_asset(mocker, asset_id, symbol):
    asset = mocker.Mock()
    asset.asset_id = asset_id
    asset.symbol = symbol
    return asset


def _mock_portfolio(mocker, portfolio_id, llm_provider="gemini", model_version="v1"):
    portfolio = mocker.Mock()
    portfolio.portfolio_id = portfolio_id
    portfolio.llm_provider = llm_provider
    portfolio.model_version = model_version
    return portfolio


def _patch_common(mocker, portfolios, watchlist, providers=None):
    mock_session = mocker.MagicMock(name="session")
    mock_get_session = mocker.patch("marketmind_ai.decision_engine.engine.get_session")
    mock_get_session.return_value.__enter__.return_value = mock_session

    mocker.patch(
        "marketmind_ai.decision_engine.engine.get_active_model_portfolios",
        return_value=portfolios,
    )
    mocker.patch(
        "marketmind_ai.decision_engine.engine.get_watchlist", return_value=watchlist
    )
    mock_build_context = mocker.patch("marketmind_ai.decision_engine.engine.build_context")
    mock_build_context.side_effect = (
        lambda session, asset_id, symbol, portfolio_id, as_of: mocker.Mock(
            asset_id=asset_id,
            symbol=symbol,
            model_dump=mocker.Mock(return_value={"asset_id": asset_id, "symbol": symbol}),
        )
    )
    mock_create_run = mocker.patch(
        "marketmind_ai.decision_engine.engine.create_model_run", return_value=1
    )
    mock_write_decision = mocker.patch(
        "marketmind_ai.decision_engine.engine.write_model_decision", return_value=99
    )
    mock_get_provider = mocker.patch(
        "marketmind_ai.decision_engine.engine.get_provider",
        side_effect=providers if providers is not None else [mocker.Mock()] * 10,
    )
    return mock_create_run, mock_write_decision, mock_build_context, mock_get_provider


class TestRunWeeklyDecisions:
    def test_writes_a_decision_per_asset_per_portfolio(self, mocker):
        watchlist = [_mock_asset(mocker, 1, "AAPL"), _mock_asset(mocker, 2, "MSFT")]
        portfolios = [_mock_portfolio(mocker, portfolio_id=10)]
        provider = mocker.Mock()
        provider.decide.return_value = Decision(decision="HOLD")
        _, mock_write_decision, _, _ = _patch_common(
            mocker, portfolios, watchlist, providers=[provider]
        )

        run_weekly_decisions(as_of=AS_OF)

        assert provider.decide.call_count == 2
        assert mock_write_decision.call_count == 2

    def test_two_active_portfolios_each_get_their_own_run_and_provider(self, mocker):
        watchlist = [_mock_asset(mocker, 1, "AAPL")]
        portfolios = [
            _mock_portfolio(mocker, portfolio_id=10, llm_provider="gemini", model_version="a"),
            _mock_portfolio(mocker, portfolio_id=20, llm_provider="gemini", model_version="b"),
        ]
        provider_a, provider_b = mocker.Mock(), mocker.Mock()
        provider_a.decide.return_value = Decision(decision="BUY")
        provider_b.decide.return_value = Decision(decision="SELL")
        mock_create_run, mock_write_decision, _, mock_get_provider = _patch_common(
            mocker, portfolios, watchlist, providers=[provider_a, provider_b]
        )

        run_weekly_decisions(as_of=AS_OF)

        assert mock_get_provider.call_count == 2
        mock_get_provider.assert_any_call(name="gemini", model="a", api_key=None)
        mock_get_provider.assert_any_call(name="gemini", model="b", api_key=None)
        assert mock_create_run.call_count == 2
        assert mock_write_decision.call_count == 2

    def test_decision_error_on_one_asset_does_not_block_the_others(self, mocker):
        watchlist = [_mock_asset(mocker, 1, "AAPL"), _mock_asset(mocker, 2, "MSFT")]
        portfolios = [_mock_portfolio(mocker, portfolio_id=10)]
        provider = mocker.Mock()
        provider.decide.side_effect = [
            DecisionError("risposta non valida"),
            Decision(decision="HOLD"),
        ]
        _, mock_write_decision, _, _ = _patch_common(
            mocker, portfolios, watchlist, providers=[provider]
        )

        run_weekly_decisions(as_of=AS_OF)

        assert provider.decide.call_count == 2
        assert mock_write_decision.call_count == 1

    def test_decision_error_on_one_portfolio_does_not_block_the_others(self, mocker):
        """Isolamento: un portfolio la cui chiamata fallisce non deve
        impedire agli altri portfolio di completare il proprio run."""
        watchlist = [_mock_asset(mocker, 1, "AAPL")]
        portfolios = [
            _mock_portfolio(mocker, portfolio_id=10),
            _mock_portfolio(mocker, portfolio_id=20),
        ]
        failing_provider = mocker.Mock()
        failing_provider.decide.side_effect = DecisionError("fallita")
        working_provider = mocker.Mock()
        working_provider.decide.return_value = Decision(decision="HOLD")
        _, mock_write_decision, _, _ = _patch_common(
            mocker, portfolios, watchlist, providers=[failing_provider, working_provider]
        )

        run_weekly_decisions(as_of=AS_OF)

        assert mock_write_decision.call_count == 1

    def test_creates_model_run_with_portfolio_metadata(self, mocker):
        portfolios = [
            _mock_portfolio(mocker, portfolio_id=10, llm_provider="gemini", model_version="v2")
        ]
        mock_create_run, _, _, _ = _patch_common(mocker, portfolios, [])

        run_weekly_decisions(as_of=AS_OF)

        mock_create_run.assert_called_once()
        _, kwargs = mock_create_run.call_args
        assert kwargs["portfolio_id"] == 10
        assert kwargs["llm_provider"] == "gemini"
        assert kwargs["model_version"] == "v2"
        assert kwargs["ts"] == AS_OF

    def test_no_active_portfolios_creates_no_run(self, mocker):
        mock_create_run, mock_write_decision, _, _ = _patch_common(mocker, [], [])

        run_weekly_decisions(as_of=AS_OF)

        mock_create_run.assert_not_called()
        mock_write_decision.assert_not_called()

    def test_empty_watchlist_writes_no_decisions_but_still_creates_a_run(self, mocker):
        """Un portfolio con watchlist vuota (mai bootstrappato) — non un errore."""
        portfolios = [_mock_portfolio(mocker, portfolio_id=10)]
        mock_create_run, mock_write_decision, _, _ = _patch_common(mocker, portfolios, [])

        run_weekly_decisions(as_of=AS_OF)

        mock_create_run.assert_called_once()
        mock_write_decision.assert_not_called()

    def test_defaults_as_of_to_now_when_omitted(self, mocker):
        portfolios = [_mock_portfolio(mocker, portfolio_id=10)]
        mock_create_run, _, _, _ = _patch_common(mocker, portfolios, [])

        run_weekly_decisions()

        _, kwargs = mock_create_run.call_args
        assert kwargs["ts"].tzinfo is not None

    def test_context_is_built_with_this_portfolios_id(self, mocker):
        """Isolamento: il context di ogni asset è costruito con il
        portfolio_id del portfolio corrente, mai di un altro."""
        watchlist = [_mock_asset(mocker, 1, "AAPL")]
        portfolios = [_mock_portfolio(mocker, portfolio_id=42)]
        provider = mocker.Mock()
        provider.decide.return_value = Decision(decision="HOLD")
        _, _, mock_build_context, _ = _patch_common(
            mocker, portfolios, watchlist, providers=[provider]
        )

        run_weekly_decisions(as_of=AS_OF)

        _, kwargs = mock_build_context.call_args
        assert kwargs["portfolio_id"] == 42


class TestInitializePortfolio:
    """`initialize_portfolio()` non si ferma alla scelta della watchlist:
    si conclude con un primo giro di `decide()` su ciò che ha appena
    scelto — un portfolio appena attivato ha subito un giudizio (anche
    HOLD) su ogni asset che osserva, non aspetta la prossima cadenza
    settimanale."""

    def _patch(self, mocker, universe, selection_symbols, reasoning=None, decide_return=None):
        mock_session = mocker.MagicMock(name="session")
        mock_get_session = mocker.patch("marketmind_ai.decision_engine.engine.get_session")
        mock_get_session.return_value.__enter__.return_value = mock_session

        portfolio = mocker.Mock(portfolio_id=7, llm_provider="gemini", model_version="v1")
        mocker.patch(
            "marketmind_ai.decision_engine.engine.get_portfolio", return_value=portfolio
        )
        mock_build_bootstrap_context = mocker.patch(
            "marketmind_ai.decision_engine.engine.build_bootstrap_context"
        )
        mock_build_bootstrap_context.return_value.model_dump.return_value = {"universe": []}
        mocker.patch(
            "marketmind_ai.decision_engine.engine.get_decision_universe", return_value=universe
        )
        provider = mocker.Mock()
        provider.select_watchlist.return_value = WatchlistSelection(
            symbols=selection_symbols, reasoning=reasoning
        )
        provider.decide.return_value = decide_return or Decision(decision="HOLD")
        mocker.patch(
            "marketmind_ai.decision_engine.engine.get_provider", return_value=provider
        )
        mock_write_watchlist = mocker.patch(
            "marketmind_ai.decision_engine.engine.write_watchlist"
        )
        mock_build_context = mocker.patch("marketmind_ai.decision_engine.engine.build_context")
        mock_build_context.side_effect = (
            lambda session, asset_id, symbol, portfolio_id, as_of: mocker.Mock(
                asset_id=asset_id,
                symbol=symbol,
                model_dump=mocker.Mock(return_value={"asset_id": asset_id, "symbol": symbol}),
            )
        )
        mock_create_run = mocker.patch(
            "marketmind_ai.decision_engine.engine.create_model_run", return_value=1
        )
        mock_write_decision = mocker.patch(
            "marketmind_ai.decision_engine.engine.write_model_decision", return_value=99
        )
        return provider, mock_write_watchlist, mock_create_run, mock_write_decision

    def test_writes_watchlist_with_resolved_asset_ids(self, mocker):
        universe = [_mock_asset(mocker, 1, "AAPL"), _mock_asset(mocker, 2, "MSFT")]
        _, mock_write_watchlist, _, _ = self._patch(mocker, universe, ["AAPL", "MSFT"])

        initialize_portfolio(7)

        args, _ = mock_write_watchlist.call_args
        assert args[1] == 7
        assert set(args[2]) == {1, 2}

    def test_discards_symbols_not_in_the_universe(self, mocker):
        universe = [_mock_asset(mocker, 1, "AAPL")]
        _, mock_write_watchlist, _, _ = self._patch(mocker, universe, ["AAPL", "NOTREAL"])

        initialize_portfolio(7)

        args, _ = mock_write_watchlist.call_args
        assert args[2] == [1]

    def test_empty_selection_writes_an_empty_watchlist(self, mocker):
        universe = [_mock_asset(mocker, 1, "AAPL")]
        _, mock_write_watchlist, _, _ = self._patch(mocker, universe, [])

        initialize_portfolio(7)

        args, _ = mock_write_watchlist.call_args
        assert args[2] == []

    def test_uses_this_portfolios_provider(self, mocker):
        universe = [_mock_asset(mocker, 1, "AAPL")]
        provider, _, _, _ = self._patch(mocker, universe, ["AAPL"])

        initialize_portfolio(7)

        provider.select_watchlist.assert_called_once()

    def test_propagates_decision_error_from_bootstrap(self, mocker):
        universe = [_mock_asset(mocker, 1, "AAPL")]
        provider, _, _, _ = self._patch(mocker, universe, ["AAPL"])
        provider.select_watchlist.side_effect = DecisionError("fallita")

        with pytest.raises(DecisionError):
            initialize_portfolio(7)

    def test_runs_a_first_decision_round_on_the_new_watchlist(self, mocker):
        universe = [_mock_asset(mocker, 1, "AAPL"), _mock_asset(mocker, 2, "MSFT")]
        provider, _, mock_create_run, mock_write_decision = self._patch(
            mocker, universe, ["AAPL", "MSFT"]
        )

        initialize_portfolio(7)

        assert provider.decide.call_count == 2
        mock_create_run.assert_called_once()
        assert mock_write_decision.call_count == 2

    def test_reuses_the_same_provider_for_bootstrap_and_first_decisions(self, mocker):
        universe = [_mock_asset(mocker, 1, "AAPL")]
        provider, _, _, _ = self._patch(mocker, universe, ["AAPL"])

        initialize_portfolio(7)

        provider.select_watchlist.assert_called_once()
        provider.decide.assert_called_once()

    def test_empty_selection_still_creates_a_run_with_no_decisions(self, mocker):
        """Nessun asset scelto: il round di decisioni gira comunque (stesso
        comportamento di run_weekly_decisions su una watchlist vuota) —
        crea un run per audit, zero decisioni, non un errore."""
        universe = [_mock_asset(mocker, 1, "AAPL")]
        provider, _, mock_create_run, mock_write_decision = self._patch(mocker, universe, [])

        initialize_portfolio(7)

        provider.decide.assert_not_called()
        mock_create_run.assert_called_once()
        mock_write_decision.assert_not_called()

    def test_decision_error_on_one_asset_does_not_block_the_first_round(self, mocker):
        universe = [_mock_asset(mocker, 1, "AAPL"), _mock_asset(mocker, 2, "MSFT")]
        provider, _, _, mock_write_decision = self._patch(mocker, universe, ["AAPL", "MSFT"])
        provider.decide.side_effect = [DecisionError("fallita"), Decision(decision="HOLD")]

        initialize_portfolio(7)

        assert provider.decide.call_count == 2
        assert mock_write_decision.call_count == 1
