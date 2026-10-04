"""Eccezioni del modulo `orchestration/`."""

from __future__ import annotations


class OrchestrationError(Exception):
    """Un comando `systemd-run`/`systemctl` invocato da `orchestration/` è
    fallito, o il binario non è disponibile sull'host. Sollevata invece di
    lasciar propagare `subprocess.CalledProcessError`/`FileNotFoundError`
    grezze, così chi chiama (`decision_engine/`, non ancora scritto) ha un
    solo tipo di eccezione da intercettare per l'intero modulo.
    """
