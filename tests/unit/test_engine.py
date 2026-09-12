"""Test unitari per `decision_engine/engine.py`.

Nessun accesso a rete/DB: sessione, lettura dell'universo, context builder e
scritture sono tutte mockate con `pytest-mock`, come per il `run()` delle
pipeline di ingestion (`test_finnhub_earnings_pipeline.py::TestRun`).
"""

from __future__ import annotations

from datetime import datetime, timezone

from marketmind_ai.decision_engine.engine import run_weekly_decisions
from marketmind_ai.llm.exceptions import DecisionError
from marketmind_ai.llm.schemas import Decision

AS_OF = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def _mock_asset(mocker, asset_id, symbol):
    asset = mocker.Mock()
    asset.asset_id = asset_id
    asset.symbol = symbol
    return asset


def _patch_common(mocker, universe):
    mock_session = mocker.MagicMock(name="session")
    mock_get_session = mocker.patch("marketmind_ai.decision_engine.engine.get_session")
    mock_get_session.return_value.__enter__.return_value = mock_session

    mocker.patch(
        "marketmind_ai.decision_engine.engine.get_decision_universe", return_value=universe
    )
    mock_build_context = mocker.patch("marketmind_ai.decision_engine.engine.build_context")
    mock_build_context.side_effect = lambda session, asset_id, symbol, as_of: mocker.Mock(
        asset_id=asset_id,
        symbol=symbol,
        model_dump=mocker.Mock(return_value={"asset_id": asset_id, "symbol": symbol}),
    )
    mock_create_run = mocker.patch(
        "marketmind_ai.decision_engine.engine.create_model_run", return_value=1
    )
    mock_write_decision = mocker.patch(
        "marketmind_ai.decision_engine.engine.write_model_decision", return_value=99
    )
    return mock_create_run, mock_write_decision, mock_build_context


class TestRunWeeklyDecisions:
    def test_writes_a_decision_per_asset(self, mocker):
        universe = [
            _mock_asset(mocker, 1, "AAPL"),
            _mock_asset(mocker, 2, "MSFT"),
        ]
        _, mock_write_decision, _ = _patch_common(mocker, universe)
        provider = mocker.Mock()
        provider.decide.return_value = Decision(decision="HOLD")

        run_weekly_decisions(
            provider, llm_provider_name="gemini", model_version="v1", as_of=AS_OF
        )

        assert provider.decide.call_count == 2
        assert mock_write_decision.call_count == 2

    def test_decision_error_on_one_asset_does_not_block_the_others(self, mocker):
        universe = [
            _mock_asset(mocker, 1, "AAPL"),
            _mock_asset(mocker, 2, "MSFT"),
        ]
        _, mock_write_decision, _ = _patch_common(mocker, universe)
        provider = mocker.Mock()
        provider.decide.side_effect = [DecisionError("risposta non valida"), Decision(decision="HOLD")]

        run_weekly_decisions(
            provider, llm_provider_name="gemini", model_version="v1", as_of=AS_OF
        )

        assert provider.decide.call_count == 2
        assert mock_write_decision.call_count == 1

    def test_creates_model_run_with_provider_metadata(self, mocker):
        mock_create_run, _, _ = _patch_common(mocker, [])
        provider = mocker.Mock()

        run_weekly_decisions(
            provider, llm_provider_name="gemini", model_version="gemini-2.5-flash", as_of=AS_OF
        )

        mock_create_run.assert_called_once()
        _, kwargs = mock_create_run.call_args
        assert kwargs["llm_provider"] == "gemini"
        assert kwargs["model_version"] == "gemini-2.5-flash"
        assert kwargs["ts"] == AS_OF

    def test_empty_universe_still_creates_a_run(self, mocker):
        mock_create_run, mock_write_decision, _ = _patch_common(mocker, [])
        provider = mocker.Mock()

        run_weekly_decisions(
            provider, llm_provider_name="gemini", model_version="v1", as_of=AS_OF
        )

        mock_create_run.assert_called_once()
        mock_write_decision.assert_not_called()

    def test_defaults_as_of_to_now_when_omitted(self, mocker):
        mock_create_run, _, mock_build_context = _patch_common(mocker, [])
        provider = mocker.Mock()

        run_weekly_decisions(provider, llm_provider_name="gemini", model_version="v1")

        _, kwargs = mock_create_run.call_args
        assert kwargs["ts"].tzinfo is not None
