"""Avvio dei job dalla dashboard: pipeline di ingestion, giro del Decision
Engine, bootstrap di un portfolio.

La dashboard non esegue nulla in proprio: avvia le unit systemd già
installate da `deploy/quadlet/deploy.sh`, cioè gli stessi container, ruoli
Postgres e secret dei timer (`systemctl --user start`, come `deploy.sh
--trigger`). Così non le servono né le chiavi API né le dipendenze del
Decision Engine, e un'azione lanciata a mano lascia la stessa traccia di un
giro schedulato (`t_ingestion_runs`, `t_model_runs`), che la dashboard
mostra.

`--no-block`: le unit sono `oneshot`, e un `start` normale aspetterebbe la
fine del container, tenendo ferma la pagina per minuti. L'esito si legge
dopo, dal DB o da `status()`.

Il backtest di un portfolio parte allo stesso modo
(`marketmind-backtest@<id>`).

Funziona dove gira systemd con quelle unit, cioè sul laptop col deploy
Quadlet. Sul server Docker (`deploy/docker/`, job in crontab) non c'è
ancora un equivalente: punto aperto.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

PIPELINE_PREFIX = "marketmind-ingest-"
PIPELINE_SUFFIX = ".service"
DECIDE_UNIT = "marketmind-decide.service"
INIT_PORTFOLIO_UNIT = "marketmind-init-portfolio@{portfolio_id}.service"
BACKTEST_UNIT = "marketmind-backtest@{portfolio_id}.service"

Runner = Callable[..., subprocess.CompletedProcess[str]]


class JobLaunchError(RuntimeError):
    """Un job non è partito: unit inesistente, systemctl assente o fallito."""


@dataclass(frozen=True)
class JobStatus:
    unit: str
    active_state: str
    result: str
    finished: str


def _valid_id(portfolio_id: int) -> int:
    if not isinstance(portfolio_id, int) or portfolio_id <= 0:
        raise JobLaunchError(f"portfolio_id non valido: {portfolio_id!r}")
    return portfolio_id


class SystemdJobLauncher:
    def __init__(self, run: Runner = subprocess.run, *, timeout_s: float = 30) -> None:
        self._run = run
        self._timeout_s = timeout_s

    def pipelines(self) -> list[str]:
        """Le pipeline con una unit installata (`marketmind-ingest-<nome>`)."""
        output = self._systemctl(
            "list-unit-files", f"{PIPELINE_PREFIX}*{PIPELINE_SUFFIX}", "--no-legend", "--plain"
        )
        names = []
        for line in output.splitlines():
            unit = line.split(maxsplit=1)[0] if line.strip() else ""
            if unit.startswith(PIPELINE_PREFIX) and unit.endswith(PIPELINE_SUFFIX):
                names.append(unit[len(PIPELINE_PREFIX) : -len(PIPELINE_SUFFIX)])
        return sorted(names)

    def trigger_pipeline(self, name: str) -> str:
        """Avvia una pipeline; il nome deve essere tra quelle installate, così
        nessun valore arbitrario arriva a `systemctl` come nome di unit."""
        if name not in self.pipelines():
            raise JobLaunchError(f"pipeline sconosciuta: {name!r}")
        return self._start(f"{PIPELINE_PREFIX}{name}{PIPELINE_SUFFIX}")

    def trigger_decision_run(self) -> str:
        """Un giro di `run-due`: apre i cicli dovuti e riprende quelli con
        asset ancora senza decisione, per tutti i portfolio attivi."""
        return self._start(DECIDE_UNIT)

    def init_portfolio(self, portfolio_id: int) -> str:
        """Bootstrap di un portfolio (`init-portfolio <id>`) tramite la unit
        template `marketmind-init-portfolio@.container`."""
        return self._start(INIT_PORTFOLIO_UNIT.format(portfolio_id=_valid_id(portfolio_id)))

    def backtest(self, portfolio_id: int) -> str:
        """Backtest dell'intero storico del portfolio (`backtest <id>`) tramite
        la unit template `marketmind-backtest@.container`; il risultato si
        aggiunge a `t_backtest_results`."""
        return self._start(BACKTEST_UNIT.format(portfolio_id=_valid_id(portfolio_id)))

    def status(self, unit: str) -> JobStatus:
        output = self._systemctl(
            "show", "-p", "ActiveState", "-p", "Result", "-p", "ExecMainExitTimestamp", unit
        )
        props = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        return JobStatus(
            unit=unit,
            active_state=props.get("ActiveState", "unknown"),
            result=props.get("Result", ""),
            finished=props.get("ExecMainExitTimestamp", ""),
        )

    def _start(self, unit: str) -> str:
        self._systemctl("start", "--no-block", unit)
        logger.info("avviata %s dalla dashboard", unit)
        return unit

    def _systemctl(self, *args: str) -> str:
        command = ["systemctl", "--user", *args]
        try:
            result: Any = self._run(
                command, check=True, capture_output=True, text=True, timeout=self._timeout_s
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip()
            raise JobLaunchError(f"{' '.join(command)} fallito: {detail or exc}") from exc
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise JobLaunchError(f"{' '.join(command)} non eseguibile: {exc}") from exc
        return result.stdout or ""
