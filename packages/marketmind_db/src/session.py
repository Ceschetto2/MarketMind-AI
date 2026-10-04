"""Funzioni di sessione del codice non ancora migrato a `Database`.

`get_engine()`/`get_session_factory()`/`get_session()` restano l'interfaccia
usata dal Decision Engine, dalla dashboard, da `writer.py` e dalle pipeline
di ingestion attuali: delegano tutte a `default_database()`
(`marketmind_db.database`), quindi una sola implementazione di sessione,
GUC del `run_id` e commit/rollback. Il codice nuovo usa direttamente
`Database.transaction()` con la policy del proprio ruolo.

`track_ingestion_run`/`track_model_run` vivono in `run_context` e sono
riesportati qui per gli import esistenti.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from marketmind_db.database import default_database
from marketmind_db.run_context import apply_run_context as _apply_run_context
from marketmind_db.run_context import track_ingestion_run, track_model_run

__all__ = [
    "get_engine",
    "get_session",
    "get_session_factory",
    "track_ingestion_run",
    "track_model_run",
    "_apply_run_context",
]


def get_engine() -> Engine:
    return default_database().engine


def get_session_factory() -> sessionmaker[Session]:
    return default_database().session_factory()


@contextmanager
def get_session() -> Iterator[Session]:
    """Context manager con commit/rollback automatico.

    Uso tipico::

        with get_session() as session:
            session.add(obj)
    """
    with default_database().session() as session:
        yield session
