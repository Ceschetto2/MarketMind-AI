"""Test unitari per `orchestration/scheduler.py`.

Nessun accesso reale a systemd: `subprocess.run` è mockato con
`pytest-mock`. Lo scopo dei test è la riga di comando costruita e la
gestione degli errori, non `systemd-run`/`systemctl` in sé.
"""

from __future__ import annotations

import subprocess

import pytest

from marketmind_ai.orchestration.exceptions import OrchestrationError
from marketmind_ai.orchestration.scheduler import (
    schedule_transient_run,
    trigger_ingestion_pipeline,
)


class TestScheduleTransientRun:
    def test_builds_systemd_run_command(self, mocker):
        mock_run = mocker.patch("marketmind_ai.orchestration.scheduler.subprocess.run")

        schedule_transient_run(["python", "-m", "some.module", "--asset-id=42"], delay_seconds=900)

        args, kwargs = mock_run.call_args
        command = args[0]
        assert command[:3] == ["systemd-run", "--user", "--on-active=900"]
        assert command[-4:] == ["python", "-m", "some.module", "--asset-id=42"]
        assert "--" in command
        assert kwargs.get("check") is True

    def test_includes_unit_name_when_given(self, mocker):
        mock_run = mocker.patch("marketmind_ai.orchestration.scheduler.subprocess.run")

        schedule_transient_run(["echo", "ciao"], delay_seconds=60, unit_name="marketmind-defer-42")

        args, _ = mock_run.call_args
        command = args[0]
        assert "--unit=marketmind-defer-42" in command

    def test_raises_orchestration_error_on_failed_command(self, mocker):
        mocker.patch(
            "marketmind_ai.orchestration.scheduler.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["systemd-run"]),
        )

        with pytest.raises(OrchestrationError):
            schedule_transient_run(["echo", "ciao"], delay_seconds=60)

    def test_raises_orchestration_error_when_systemd_run_missing(self, mocker):
        mocker.patch(
            "marketmind_ai.orchestration.scheduler.subprocess.run",
            side_effect=FileNotFoundError("systemd-run non trovato"),
        )

        with pytest.raises(OrchestrationError):
            schedule_transient_run(["echo", "ciao"], delay_seconds=60)


class TestTriggerIngestionPipeline:
    def test_builds_systemctl_start_command(self, mocker):
        mock_run = mocker.patch("marketmind_ai.orchestration.scheduler.subprocess.run")

        trigger_ingestion_pipeline("finnhub-news")

        args, kwargs = mock_run.call_args
        assert args[0] == [
            "systemctl",
            "--user",
            "start",
            "marketmind-ingest-finnhub-news.service",
        ]
        assert kwargs.get("check") is True

    def test_raises_orchestration_error_on_failed_command(self, mocker):
        mocker.patch(
            "marketmind_ai.orchestration.scheduler.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["systemctl"]),
        )

        with pytest.raises(OrchestrationError):
            trigger_ingestion_pipeline("finnhub-news")
