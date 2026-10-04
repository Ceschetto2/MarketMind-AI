"""Collegamento `run_id` dei trigger generici di audit/snapshot.

`audit.fn_audit_log()` (0001) legge la GUC `marketmind.ingestion_run_id`,
`portfolio.fn_portfolio_snapshot()`/`fn_portfolio_position_snapshot()`
(0002) leggono `marketmind.model_run_id`: è lo strato applicativo a doverle
impostare a ogni transazione. `track_ingestion_run()`/`track_model_run()`
sono `ContextVar`, non parametri di sessione: chi apre un run lo dichiara
una volta sola (`IngestionRunAudit`, il loop del Decision Engine) e ogni
sessione aperta dentro quel blocco — da `Database.session()`, senza che il
codice che la usa sappia nulla del meccanismo — imposta la GUC da sé.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.orm import Session

INGESTION_RUN_GUC = "marketmind.ingestion_run_id"
MODEL_RUN_GUC = "marketmind.model_run_id"

_ingestion_run_id: ContextVar[int | None] = ContextVar("_ingestion_run_id", default=None)
_model_run_id: ContextVar[int | None] = ContextVar("_model_run_id", default=None)


def current_ingestion_run_id() -> int | None:
    return _ingestion_run_id.get()


def current_model_run_id() -> int | None:
    return _model_run_id.get()


@contextmanager
def track_ingestion_run(run_id: int) -> Iterator[None]:
    """Marca il blocco corrente come appartenente al run di ingestion `run_id`."""
    token = _ingestion_run_id.set(run_id)
    try:
        yield
    finally:
        _ingestion_run_id.reset(token)


@contextmanager
def track_model_run(run_id: int) -> Iterator[None]:
    """Marca il blocco corrente come appartenente al run del motore decisionale `run_id`."""
    token = _model_run_id.set(run_id)
    try:
        yield
    finally:
        _model_run_id.reset(token)


def set_local_setting(session: Session, name: str, value: str | int) -> None:
    """Imposta un parametro Postgres valido solo per la transazione corrente.

    `SET LOCAL <nome> = <valore>` non accetta bind parameter (non è una
    query preparabile): `set_config(nome, valore, true)` è l'equivalente che
    li accetta, con la stessa durata — resettato da solo al commit/rollback.
    """
    session.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": name, "value": str(value)},
    )


def apply_run_context(session: Session) -> None:
    """Imposta le GUC dei run attualmente tracciati. Va eseguita come primo
    statement della sessione: grazie all'autobegin di SQLAlchemy copre
    l'intera transazione."""
    ingestion_run_id = _ingestion_run_id.get()
    if ingestion_run_id is not None:
        set_local_setting(session, INGESTION_RUN_GUC, ingestion_run_id)
    model_run_id = _model_run_id.get()
    if model_run_id is not None:
        set_local_setting(session, MODEL_RUN_GUC, model_run_id)
