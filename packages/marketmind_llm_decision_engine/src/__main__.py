"""Entry point del Decision Engine.

    python -m marketmind_llm_decision_engine run-due [--dry-run]
    python -m marketmind_llm_decision_engine init-portfolio <portfolio_id> [--dry-run]
    python -m marketmind_llm_decision_engine backtest <portfolio_id> <run_id> [--no-save]

`run-due` è il comando del timer orario condiviso (`marketmind-decide`): fa
girare i portfolio `model` il cui turno è dovuto. `init-portfolio` è il
bootstrap di un portfolio (watchlist scelta dall'LLM più un primo giro di
decisioni). `backtest` rigioca i trade di un run e ne salva le metriche.
`--dry-run` esegue tutto, chiamate LLM comprese, ma annulla ogni scrittura.

Gira come ruolo `marketmind_app` con la policy `APP` (scrittura solo su
`decisions`/`portfolio`). Exit code: 0 se nessun giro è `failed`, 1
altrimenti (e per un bootstrap o un backtest falliti), 143 su SIGTERM,
convertito in eccezione così i run aperti si chiudono `failed` invece di
restare `running`.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from types import FrameType

from marketmind_common.logging_config import configure_logging
from marketmind_db.access import APP
from marketmind_db.database import Database, DatabaseSettings
from marketmind_llm_decision_engine.decision_engine.engine import DecisionEngine
from marketmind_llm_decision_engine.decision_engine.store import PostgresDecisionStore
from marketmind_llm_decision_engine.llm.exceptions import DecisionError
from marketmind_llm_decision_engine.llm.factory import get_provider

logger = logging.getLogger("marketmind_llm_decision_engine")

EXIT_INTERRUPTED = 128 + signal.SIGTERM


class Interrupted(BaseException):
    """SIGTERM ricevuto: non un errore di un singolo giro, chiude tutto."""


def _raise_interrupted(signum: int, frame: FrameType | None) -> None:
    raise Interrupted(f"interrotto da {signal.Signals(signum).name}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m marketmind_llm_decision_engine")
    commands = parser.add_subparsers(dest="command", required=True)
    run_due = commands.add_parser("run-due", help="giri dei portfolio dovuti ora")
    run_due.add_argument("--dry-run", action="store_true")
    init = commands.add_parser("init-portfolio", help="bootstrap di un portfolio")
    init.add_argument("portfolio_id", type=int)
    init.add_argument("--dry-run", action="store_true")
    backtest = commands.add_parser("backtest", help="backtest dei trade di un run")
    backtest.add_argument("portfolio_id", type=int)
    backtest.add_argument("run_id", type=int)
    backtest.add_argument("--no-save", action="store_true", help="non scrivere in t_backtest_results")
    return parser


def _run(args: argparse.Namespace, db: Database) -> int:
    if args.command == "backtest":
        # Import qui, non in testa: vectorbt (e numba) servono solo al
        # backtest, e rallenterebbero ogni `run-due` orario.
        from marketmind_llm_decision_engine.backtest.engine import (
            Backtester,
            NoTradesForRunError,
            PostgresBacktestStore,
        )

        try:
            metrics, backtest_id = Backtester(PostgresBacktestStore(db)).run(
                portfolio_id=args.portfolio_id,
                run_id=args.run_id,
                as_of=datetime.now(timezone.utc),
                save=not args.no_save,
            )
        except (NoTradesForRunError, NotImplementedError) as exc:
            logger.error("backtest non eseguibile: %s", exc)
            return 1
        logger.info("backtest %s: %s", backtest_id, metrics)
        return 0

    engine = DecisionEngine(PostgresDecisionStore(db), get_provider)
    if args.command == "run-due":
        results = engine.run_due()
        return 1 if any(r.status == "failed" for r in results) else 0

    try:
        result = engine.initialize_portfolio(args.portfolio_id)
    except DecisionError as exc:
        logger.error("bootstrap del portfolio %s fallito: %s", args.portfolio_id, exc)
        return 1
    return 1 if result.status == "failed" else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    configure_logging()
    dry_run = getattr(args, "dry_run", False)
    previous = signal.signal(signal.SIGTERM, _raise_interrupted)
    try:
        with Database(DatabaseSettings.from_env(), policy=APP, dry_run=dry_run) as db:
            code = _run(args, db)
    except Interrupted as exc:
        logger.error("%s", exc)
        return EXIT_INTERRUPTED
    finally:
        signal.signal(signal.SIGTERM, previous)
    if dry_run:
        logger.info("dry run: nessuna scrittura committata")
    return code


if __name__ == "__main__":
    sys.exit(main())
