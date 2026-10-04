"""Test unitari del registro e dell'entry point (`python -m marketmind_pipelines`)."""

from __future__ import annotations

import signal

import pytest

from marketmind_db.access import INGESTION
from marketmind_pipelines import __main__ as cli
from marketmind_pipelines.base import PerSymbolPipeline, PipelineInterrupted, RunResult
from marketmind_pipelines.registry import PIPELINES


class TestRegistro:
    def test_otto_pipeline_registrate_col_proprio_nome(self):
        assert sorted(PIPELINES) == [
            "finnhub-earnings",
            "finnhub-news",
            "fmp",
            "fred",
            "gdelt-ngrams",
            "universe-csv",
            "yfinance-assets",
            "yfinance-prices",
        ]
        assert all(cls.name == name for name, cls in PIPELINES.items())

    @pytest.mark.parametrize("name", sorted(PIPELINES))
    def test_audit_source_ammesso_dal_check(self, name):
        allowed = {"yfinance", "gdelt", "gdelt-ngrams", "gdelt-doc", "finnhub", "fred", "fmp", "universe-csv"}
        assert PIPELINES[name].audit_source in allowed


def _result(status: str) -> RunResult:
    return RunResult(pipeline="x", status=status, run_id=1, targets_total=1, rows_written=0)


@pytest.fixture
def fake_db(mocker):
    return mocker.patch.object(cli, "Database")


@pytest.fixture(autouse=True)
def no_logging_setup(mocker):
    mocker.patch.object(cli, "configure_logging")


class TestRun:
    def test_crea_il_database_con_policy_ingestion(self, mocker, fake_db):
        run = mocker.patch.object(PIPELINES["fred"], "run", return_value=_result("success"))

        assert cli.main(["run", "fred"]) == 0

        run.assert_called_once()
        assert fake_db.call_args.kwargs["policy"] is INGESTION
        assert fake_db.call_args.kwargs["dry_run"] is False

    def test_dry_run(self, mocker, fake_db):
        mocker.patch.object(PIPELINES["fred"], "run", return_value=_result("success"))

        cli.main(["run", "fred", "--dry-run"])

        assert fake_db.call_args.kwargs["dry_run"] is True

    @pytest.mark.parametrize(("status", "code"), [("success", 0), ("partial", 0), ("failed", 1)])
    def test_exit_code_per_esito(self, mocker, fake_db, status, code):
        mocker.patch.object(PIPELINES["fred"], "run", return_value=_result(status))

        assert cli.main(["run", "fred"]) == code

    def test_symbols_passati_alle_pipeline_per_ticker(self, mocker, fake_db):
        captured = {}

        def fake_run(self):
            captured["symbols"] = self.symbols
            return _result("success")

        mocker.patch.object(PIPELINES["yfinance-prices"], "run", fake_run)

        cli.main(["run", "yfinance-prices", "--symbols", "AAPL, MSFT"])

        assert captured["symbols"] == ["AAPL", "MSFT"]

    def test_symbols_rifiutato_per_pipeline_non_per_ticker(self, fake_db):
        assert not issubclass(PIPELINES["fred"], PerSymbolPipeline)
        with pytest.raises(SystemExit):
            cli.main(["run", "fred", "--symbols", "AAPL"])

    def test_pipeline_sconosciuta(self, fake_db):
        with pytest.raises(SystemExit):
            cli.main(["run", "non-esiste"])

    def test_sigterm_convertito_in_interruzione(self, mocker, fake_db):
        def fake_run(self):
            signal.raise_signal(signal.SIGTERM)
            return _result("success")

        mocker.patch.object(PIPELINES["fred"], "run", fake_run)
        previous = signal.getsignal(signal.SIGTERM)

        assert cli.main(["run", "fred"]) == 128 + signal.SIGTERM
        assert signal.getsignal(signal.SIGTERM) == previous

    def test_interruzione_propagata_dalla_pipeline(self, mocker, fake_db):
        mocker.patch.object(PIPELINES["fred"], "run", side_effect=PipelineInterrupted("SIGTERM"))

        assert cli.main(["run", "fred"]) == 128 + signal.SIGTERM


class TestList:
    def test_elenca_le_pipeline(self, capsys):
        assert cli.main(["list"]) == 0

        out = capsys.readouterr().out
        assert "yfinance-prices" in out and "fmp" in out
