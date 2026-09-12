"""Primitivi di scheduling ad-hoc per il rinvio delle decisioni.

Dettaglio del disegno in
`Market Mind AI - Docs/Decision Engine/01_rinvio_decisione_e_orchestration.md`
(vault Obsidian esterno, percorso in `CLAUDE.md`). Due funzioni distinte per
un motivo di permessi, non di comodità: `trigger_ingestion_pipeline` passa
dal container Quadlet già esistente della pipeline (ruolo Postgres
`marketmind_ingestion`), `schedule_transient_run` schedula genericamente
l'esecuzione futura di un comando qualunque — tipicamente la ri-decisione
vera e propria, che gira invece con `marketmind_app` in sola lettura.
Nessuna delle due tocca Postgres direttamente: sono wrapper su
`systemd`/`systemctl`.

`decision_engine/` (non ancora scritto) è l'unico chiamante previsto:
comporrà queste primitive per rispondere a una `DeferralRequest`
(`llm.schemas`), tipicamente schedulando con `schedule_transient_run` un
comando che, allo scadere, invoca `trigger_ingestion_pipeline` se serve un
refresh dati e poi richiama la ri-decisione — quell'entry point combinato
non esiste ancora, perché non esiste ancora `decision_engine/` da invocare.
"""

from __future__ import annotations

import logging
import subprocess

from marketmind_ai.orchestration.exceptions import OrchestrationError

logger = logging.getLogger(__name__)


def schedule_transient_run(
    command: list[str], delay_seconds: int, unit_name: str | None = None
) -> None:
    """Schedula l'esecuzione futura di `command` con una unit systemd
    transiente (`systemd-run --user --on-active=<delay_seconds>`), mai un
    file `.timer` scritto su disco — nessuna unit persistita nel
    git-tracked `systemd/`, nessun drift da riconciliare.
    """
    args = ["systemd-run", "--user", f"--on-active={delay_seconds}"]
    if unit_name:
        args.append(f"--unit={unit_name}")
    args.append("--")
    args.extend(command)

    try:
        subprocess.run(args, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise OrchestrationError(
            f"schedule_transient_run fallita per {command!r} tra {delay_seconds}s: {exc}"
        ) from exc

    logger.info("schedulata esecuzione futura tra %ds: %r", delay_seconds, command)


def trigger_ingestion_pipeline(pipeline: str) -> None:
    """Avvia subito il container Quadlet già esistente di una pipeline di
    ingestion (`systemctl --user start marketmind-ingest-<pipeline>.service`),
    stesso meccanismo di `deploy.sh --trigger <pipeline>`.

    Nessun parametro ad-hoc (simbolo, finestra) per ora: come farli
    raggiungere il container resta un punto aperto (unit systemd "template"
    contro un bypass diretto del Quadlet statico) — vedi il documento di
    disegno in testa al modulo.
    """
    unit = f"marketmind-ingest-{pipeline}.service"

    try:
        subprocess.run(["systemctl", "--user", "start", unit], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise OrchestrationError(f"trigger_ingestion_pipeline fallito per {unit}: {exc}") from exc

    logger.info("avviata pipeline ad-hoc: %s", unit)
