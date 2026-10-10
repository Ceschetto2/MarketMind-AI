"""Classi base delle pipeline di ingestion.

`BasePipeline.run()` fissa la sequenza di ogni run, uguale per tutte le
pipeline: audit in `audit.t_ingestion_runs` → `setup()` → per ogni target
`extract()` → `transform()` → `load()` → `teardown()` → esito. Le
sottoclassi definiscono solo cosa cambia: quali sono i target, come si
scaricano, come diventano record Pydantic, con quale sink si scrivono.

Un target è l'unità di lavoro isolata: un ticker, un indicatore FRED, una
coppia (ticker, endpoint) per FMP, oppure l'intero run per le pipeline con
un solo fetch (`BulkPipeline`). L'errore di un target viene registrato e il
run prosegue con i successivi; l'esito finale dipende da quanti target sono
falliti (`outcome()`):

- `success`: nessun target fallito (anche zero target, o target senza dati);
- `partial`: almeno un target fallito, fino a `failure_threshold` compreso;
- `failed`: oltre `failure_threshold`, oppure un'eccezione fuori dai target
  (universo illeggibile, CSV malformato, ...), che viene anche rilanciata.

`PipelineInterrupted` (un `BaseException`, sollevato dal gestore di SIGTERM
dell'entry point) non viene isolato come errore di target: chiude il run
come `failed` invece di lasciarlo `running` per sempre.
"""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

from marketmind_db.audit import IngestionRunAudit, RunStatus, run_outcome
from marketmind_db.database import Database
from marketmind_pipelines.lookups import universe_symbols
from marketmind_pipelines.sinks import RecordSink

logger = logging.getLogger(__name__)

class PipelineInterrupted(BaseException):
    """Run interrotto dall'esterno (SIGTERM da systemd/Podman)."""


@dataclass(frozen=True)
class TargetFailure:
    target: str
    error: str


@dataclass
class RunResult:
    pipeline: str
    status: RunStatus
    run_id: int | None
    targets_total: int
    rows_written: int
    failures: list[TargetFailure] = field(default_factory=list)


class BasePipeline[TargetT, RawT, RecordT](ABC):
    # Nome nel registro e nell'entry point (`python -m marketmind_pipelines run <name>`).
    name: ClassVar[str]
    # Valore di `t_ingestion_runs.source` (ammesso da `ck_t_ingestion_runs_source`).
    audit_source: ClassVar[str]
    target_table: ClassVar[str]
    # Sink usato dal `load()` di default.
    sink: ClassVar[type[RecordSink[Any]]]
    # Frazione di target falliti oltre la quale il run è `failed`.
    failure_threshold: ClassVar[float] = 0.5
    # Quanti errori di target riportare in `error_message` dell'audit.
    MAX_REPORTED_FAILURES: ClassVar[int] = 20

    def __init__(self, db: Database) -> None:
        self.db = db

    # --- da definire nelle sottoclassi ------------------------------------

    @abstractmethod
    def targets(self) -> Iterable[TargetT]: ...

    @abstractmethod
    def extract(self, target: TargetT) -> RawT: ...

    @abstractmethod
    def transform(self, target: TargetT, raw: RawT) -> Sequence[RecordT]: ...

    # --- hook con default -------------------------------------------------

    def load(self, records: Sequence[RecordT]) -> int:
        """Scrive i record di un target in una transazione propria: un target
        fallito in scrittura non annulla quelli già scritti."""
        with self.db.transaction() as tx:
            return self.sink(tx).write(records)

    def setup(self) -> None:
        """Prima dei target, dentro il run tracciato (es. leggere l'universo)."""

    def teardown(self) -> None:
        """Dopo i target, anche in caso di errore."""

    def between_targets(self) -> None:
        """Tra un target e il successivo (es. una pausa per il rate limit)."""

    def describe_target(self, target: TargetT) -> str:
        return str(target)

    # --- sequenza fissa ---------------------------------------------------

    @classmethod
    def outcome(cls, total: int, failed: int) -> RunStatus:
        return run_outcome(total, failed, cls.failure_threshold)

    def run(self) -> RunResult:
        failures: list[TargetFailure] = []
        with IngestionRunAudit(self.db, self.audit_source, self.target_table) as audit:
            self.setup()
            try:
                targets = list(self.targets())
                logger.info("%s: %d target", self.name, len(targets))
                for i, target in enumerate(targets):
                    if i > 0:
                        self.between_targets()
                    try:
                        records = self.transform(target, self.extract(target))
                        written = self.load(records) if records else 0
                    except Exception as exc:
                        label = self.describe_target(target)
                        logger.exception("%s: target %s fallito, proseguo", self.name, label)
                        failures.append(TargetFailure(label, f"{type(exc).__name__}: {exc}"))
                        continue
                    audit.rows_written += written
            finally:
                self.teardown()

            status = self.outcome(len(targets), len(failures))
            if status != "success":
                summary = self._failure_summary(len(targets), failures)
                if status == "partial":
                    audit.mark_partial(summary)
                else:
                    audit.mark_failed(summary)

        logger.info(
            "%s: %s, %d righe scritte, %d/%d target falliti",
            self.name, status, audit.rows_written, len(failures), len(targets),
        )
        return RunResult(
            pipeline=self.name,
            status=status,
            run_id=audit.run_id,
            targets_total=len(targets),
            rows_written=audit.rows_written,
            failures=failures,
        )

    def _failure_summary(self, total: int, failures: list[TargetFailure]) -> str:
        shown = failures[: self.MAX_REPORTED_FAILURES]
        details = "; ".join(f"{f.target}: {f.error}" for f in shown)
        more = len(failures) - len(shown)
        suffix = f"; ... e altri {more}" if more else ""
        return f"{len(failures)}/{total} target falliti: {details}{suffix}"


class PerSymbolPipeline[RawT, RecordT](BasePipeline[Any, RawT, RecordT]):
    """Un target per ticker: di default l'universo osservato, oppure i
    `symbols` passati esplicitamente (run ad-hoc su un sottoinsieme, es. dal
    flag `--symbols` dell'entry point). Pausa casuale tra un ticker e
    l'altro, per restare sotto i rate limit della fonte."""

    delay_between_targets: ClassVar[tuple[float, float]] = (0.0, 0.0)

    def __init__(
        self,
        db: Database,
        *,
        symbols: Sequence[str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(db)
        self.symbols = list(symbols) if symbols is not None else None
        self._sleep = sleep

    def default_symbols(self) -> list[str]:
        with self.db.transaction() as tx:
            return universe_symbols(tx)

    def symbols_to_process(self) -> list[str]:
        return self.symbols if self.symbols is not None else self.default_symbols()

    def targets(self) -> Iterable[Any]:
        return self.symbols_to_process()

    def between_targets(self) -> None:
        low, high = self.delay_between_targets
        if high > 0:
            self._sleep(random.uniform(low, high))


class BulkPipeline[RawT, RecordT](BasePipeline[None, RawT, RecordT]):
    """Un solo target per run: un fetch che copre tutto (il calendario
    earnings di tutte le aziende, un file GDELT, il CSV di seed). Se il fetch
    fallisce il run è `failed`."""

    @abstractmethod
    def fetch(self) -> RawT: ...

    @abstractmethod
    def parse(self, raw: RawT) -> Sequence[RecordT]: ...

    def targets(self) -> Iterable[None]:
        return [None]

    def extract(self, target: None) -> RawT:
        return self.fetch()

    def transform(self, target: None, raw: RawT) -> Sequence[RecordT]:
        return self.parse(raw)

    def describe_target(self, target: None) -> str:
        return self.name
