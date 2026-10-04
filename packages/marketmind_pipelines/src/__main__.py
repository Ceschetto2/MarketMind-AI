"""Entry point unico delle pipeline di ingestion.

    python -m marketmind_pipelines list
    python -m marketmind_pipelines run <nome> [--symbols AAPL,MSFT] [--dry-run]

È il comando che le unit Quadlet eseguono (`Exec=`), uno per container.
`--symbols` limita una pipeline per ticker a un sottoinsieme (run ad-hoc,
es. un rinvio del Decision Engine che richiede dati freschi su un solo
asset); `--dry-run` esegue tutto ma annulla ogni transazione.

Exit code: 0 per `success`/`partial` (un ticker fallito non deve far
risultare fallita la unit systemd: l'esito resta in
`audit.t_ingestion_runs`), 1 per `failed`, 128+15 se interrotto da SIGTERM.
SIGTERM (stop del container da systemd/Podman) è convertito in
`PipelineInterrupted`, così il run viene chiuso come `failed` invece di
restare `running` per sempre in `audit.t_ingestion_runs`.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from collections.abc import Sequence
from types import FrameType

from marketmind_common.logging_config import configure_logging
from marketmind_db.access import INGESTION
from marketmind_db.database import Database, DatabaseSettings
from marketmind_pipelines.base import PerSymbolPipeline, PipelineInterrupted
from marketmind_pipelines.registry import PIPELINES

logger = logging.getLogger(__name__)

EXIT_INTERRUPTED = 128 + signal.SIGTERM


def _raise_interrupted(signum: int, frame: FrameType | None) -> None:
    raise PipelineInterrupted(f"interrotto da {signal.Signals(signum).name}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m marketmind_pipelines")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="elenca le pipeline disponibili")
    run = commands.add_parser("run", help="esegue una pipeline")
    run.add_argument("pipeline", choices=sorted(PIPELINES))
    run.add_argument("--symbols", help="solo questi ticker, separati da virgola (pipeline per ticker)")
    run.add_argument("--dry-run", action="store_true", help="esegue tutto ma annulla ogni scrittura")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "list":
        for name, cls in sorted(PIPELINES.items()):
            print(f"{name}\t{cls.target_table}")
        return 0

    configure_logging()
    pipeline_cls = PIPELINES[args.pipeline]
    kwargs = {}
    if args.symbols:
        if not issubclass(pipeline_cls, PerSymbolPipeline):
            parser.error(f"--symbols non si applica a {args.pipeline}: non è una pipeline per ticker")
        kwargs["symbols"] = [s.strip() for s in args.symbols.split(",") if s.strip()]

    previous = signal.signal(signal.SIGTERM, _raise_interrupted)
    try:
        with Database(DatabaseSettings.from_env(), policy=INGESTION, dry_run=args.dry_run) as db:
            result = pipeline_cls(db, **kwargs).run()
    except PipelineInterrupted as exc:
        logger.error("%s: %s", args.pipeline, exc)
        return EXIT_INTERRUPTED
    finally:
        signal.signal(signal.SIGTERM, previous)
    if args.dry_run:
        logger.info("dry run: nessuna scrittura committata")
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
