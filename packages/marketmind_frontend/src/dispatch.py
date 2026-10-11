"""Dispatcher della coda dei job: la parte che gira nel container frontend.

    python -m marketmind_frontend.dispatch claim
    python -m marketmind_frontend.dispatch fail <job_request_id> <motivo...>

`claim` prende in carico la richiesta pendente più vecchia
(`jobs.JobQueue.claim`) e stampa su stdout `<id> <unit>`, la unit systemd da
avviare; non stampa nulla se non ci sono richieste. `fail` registra che
l'avvio della unit è fallito. Lo script sull'host
(`deploy/systemd/marketmind_frontend/marketmind-dispatch.sh`, lanciato da un
timer ogni minuto) chiama `claim`, avvia la unit con `systemctl --user start
--no-block` e, se l'avvio fallisce, chiama `fail`: il container non ha alcun
accesso all'host, l'host non decide nulla.

Gira come ruolo `marketmind_app` (policy `APP`), con le credenziali di
`connections.py`. I log vanno su stderr: stdout porta solo la riga per lo
script.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime, timezone

from marketmind_common.logging_config import configure_logging
from marketmind_db.database import Database
from marketmind_frontend.connections import dashboard_databases
from marketmind_frontend.jobs import JobQueue, JobRequest

_UNITS = {
    "pipeline": "marketmind-ingest-{target}.service",
    "decide": "marketmind-decide.service",
    "init-portfolio": "marketmind-init-portfolio@{target}.service",
    "backtest": "marketmind-backtest@{target}.service",
}


def unit_for(request: JobRequest) -> str:
    """La unit systemd del deploy Quadlet che esegue la richiesta. Il target
    è già stato validato all'accodamento (`jobs.normalize_job`)."""
    return _UNITS[request.kind].format(target=request.target)


def _admin_database() -> Database:
    return dashboard_databases().admin.database


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m marketmind_frontend.dispatch")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("claim", help="prende in carico la richiesta più vecchia e stampa la unit")
    fail = commands.add_parser("fail", help="registra il fallimento dell'avvio di una richiesta")
    fail.add_argument("job_request_id", type=int)
    fail.add_argument("message", nargs="+")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging()
    now = datetime.now(timezone.utc)
    with _admin_database().transaction() as tx:
        queue = JobQueue(tx)
        if args.command == "claim":
            request = queue.claim(now=now)
            if request is not None:
                print(f"{request.job_request_id} {unit_for(request)}")
        else:
            queue.fail(args.job_request_id, " ".join(args.message), now=now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
