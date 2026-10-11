"""Test unitari del lancio dei job dalla dashboard (`marketmind_frontend.jobs`):
`systemctl --user` è sostituito da un runner finto, nessun processo reale."""

from __future__ import annotations

import subprocess

import pytest

from marketmind_frontend.jobs import JobLaunchError, SystemdJobLauncher

LIST_OUTPUT = (
    "marketmind-ingest-finnhub-news.service     generated -\n"
    "marketmind-ingest-fred.service             generated -\n"
    "marketmind-ingest-yfinance-prices.service  generated -\n"
)


class FakeRunner:
    def __init__(self, stdout: str = "", returncode: int = 0, exc: Exception | None = None):
        self.calls: list[list[str]] = []
        self.stdout = stdout
        self.returncode = returncode
        self.exc = exc

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        if self.exc is not None:
            raise self.exc
        if kwargs.get("check") and self.returncode != 0:
            raise subprocess.CalledProcessError(self.returncode, args, output=self.stdout, stderr="boom")
        return subprocess.CompletedProcess(args, self.returncode, stdout=self.stdout, stderr="")


def test_pipelines_are_read_from_installed_units():
    runner = FakeRunner(stdout=LIST_OUTPUT)

    assert SystemdJobLauncher(runner).pipelines() == ["finnhub-news", "fred", "yfinance-prices"]
    assert runner.calls[0][:4] == ["systemctl", "--user", "list-unit-files", "marketmind-ingest-*.service"]


def test_trigger_pipeline_starts_its_unit_without_waiting():
    runner = FakeRunner(stdout=LIST_OUTPUT)

    unit = SystemdJobLauncher(runner).trigger_pipeline("fred")

    assert unit == "marketmind-ingest-fred.service"
    assert runner.calls[-1] == ["systemctl", "--user", "start", "--no-block", "marketmind-ingest-fred.service"]


def test_trigger_unknown_pipeline_is_rejected_before_systemctl_start():
    runner = FakeRunner(stdout=LIST_OUTPUT)

    with pytest.raises(JobLaunchError, match="sconosciuta"):
        SystemdJobLauncher(runner).trigger_pipeline("fred; rm -rf ~")

    assert all("start" not in call for call in runner.calls)


def test_trigger_decision_run_starts_the_shared_decide_unit():
    runner = FakeRunner()

    assert SystemdJobLauncher(runner).trigger_decision_run() == "marketmind-decide.service"
    assert runner.calls == [["systemctl", "--user", "start", "--no-block", "marketmind-decide.service"]]


def test_init_portfolio_starts_the_template_instance():
    runner = FakeRunner()

    unit = SystemdJobLauncher(runner).init_portfolio(7)

    assert unit == "marketmind-init-portfolio@7.service"
    assert runner.calls == [["systemctl", "--user", "start", "--no-block", "marketmind-init-portfolio@7.service"]]


@pytest.mark.parametrize("bad_id", [0, -3])
def test_init_portfolio_rejects_non_positive_ids(bad_id):
    with pytest.raises(JobLaunchError):
        SystemdJobLauncher(FakeRunner()).init_portfolio(bad_id)


def test_systemctl_failure_becomes_job_launch_error():
    launcher = SystemdJobLauncher(FakeRunner(returncode=5))

    with pytest.raises(JobLaunchError, match="marketmind-decide.service"):
        launcher.trigger_decision_run()


def test_missing_systemctl_becomes_job_launch_error():
    launcher = SystemdJobLauncher(FakeRunner(exc=FileNotFoundError("systemctl")))

    with pytest.raises(JobLaunchError):
        launcher.trigger_decision_run()


def test_status_parses_systemctl_show():
    runner = FakeRunner(
        stdout="ActiveState=failed\nResult=exit-code\nExecMainExitTimestamp=Sun 2026-10-11 20:00:08 CEST\n"
    )

    status = SystemdJobLauncher(runner).status("marketmind-decide.service")

    assert status.active_state == "failed"
    assert status.result == "exit-code"
    assert status.finished == "Sun 2026-10-11 20:00:08 CEST"
