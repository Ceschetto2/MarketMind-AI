"""Engine e session factory SQLAlchemy per Postgres+TimescaleDB.

L'engine è creato lazy (alla prima chiamata a `get_engine()`) così che
importare questo modulo non richieda una connessione al database né la
presenza di `.env` — utile per test unitari e per gli script di ingestion
che validano solo i record Pydantic senza scrivere su Postgres.

Porta anche il collegamento `run_id` dei trigger generici di audit/snapshot
(`marketmind.ingestion_run_id` in `audit.fn_audit_log()`, 0001;
`marketmind.model_run_id` in `portfolio.fn_portfolio_snapshot()`/
`fn_portfolio_position_snapshot()`, 0002) — entrambe le migration lo
documentavano come responsabilità di questo strato ("lo strato di
scrittura in `db/` la imposta con `SET LOCAL`"), ma fino al fix di
`Market Mind AI - Docs/tasks/2026-09-14-run-id-guc-linkage.md` nessun
codice lo faceva davvero: ogni riga storica in `audit.t_audit_logs`/
`t_portfolio_snapshots`/`t_portfolio_position_snapshots` ha `run_id` NULL.
`track_ingestion_run()`/`track_model_run()` sono `ContextVar`, non
parametri di `get_session()`: così `db/writer.py` (`ingestion_run()`) e
`decision_engine/engine.py` possono dichiarare "questo blocco appartiene al
run N" una volta sola, e ogni `get_session()` aperta dentro quel blocco —
dai pipeline/dal loop di decisione, senza sapere nulla del meccanismo —
la applica automaticamente. `SET LOCAL` vale solo per la transazione
corrente: qui è il primo statement eseguito su ogni sessione, quindi
copre l'intera transazione di quella sessione grazie all'autobegin di
SQLAlchemy.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from marketmind_ai.config import get_database_url

_ingestion_run_id: ContextVar[int | None] = ContextVar("_ingestion_run_id", default=None)
_model_run_id: ContextVar[int | None] = ContextVar("_model_run_id", default=None)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(get_database_url(), pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


@contextmanager
def track_ingestion_run(run_id: int) -> Iterator[None]:
    """Marca il blocco corrente come appartenente al run di ingestion
    `run_id`: ogni `get_session()` aperta al suo interno imposta
    `marketmind.ingestion_run_id`, letto da `audit.fn_audit_log()`. Usato da
    `db/writer.py` (`ingestion_run()`), non dai singoli script di
    ingestion."""
    token = _ingestion_run_id.set(run_id)
    try:
        yield
    finally:
        _ingestion_run_id.reset(token)


@contextmanager
def track_model_run(run_id: int) -> Iterator[None]:
    """Marca il blocco corrente come appartenente al run del motore
    decisionale `run_id`: ogni `get_session()` aperta al suo interno imposta
    `marketmind.model_run_id`, letto dai trigger di snapshot del
    portfolio. Usato da `decision_engine/engine.py`."""
    token = _model_run_id.set(run_id)
    try:
        yield
    finally:
        _model_run_id.reset(token)


def _set_local_guc(session: Session, name: str, run_id: int) -> None:
    # `SET LOCAL <guc> = <value>` non accetta bind parameter in Postgres
    # (non è una query preparabile) — `set_config()` è la funzione
    # equivalente che li accetta, con lo stesso terzo argomento `true` per
    # renderla locale alla transazione corrente (stessa durata di `SET
    # LOCAL`, resettata da sola al commit/rollback).
    session.execute(
        text("SELECT set_config(:name, :value, true)"),
        {"name": name, "value": str(run_id)},
    )


def _apply_run_context(session: Session) -> None:
    ingestion_run_id = _ingestion_run_id.get()
    if ingestion_run_id is not None:
        _set_local_guc(session, "marketmind.ingestion_run_id", ingestion_run_id)
    model_run_id = _model_run_id.get()
    if model_run_id is not None:
        _set_local_guc(session, "marketmind.model_run_id", model_run_id)


@contextmanager
def get_session() -> Iterator[Session]:
    """Context manager con commit/rollback automatico.

    Uso tipico::

        with get_session() as session:
            session.add(obj)
    """
    session = get_session_factory()()
    try:
        _apply_run_context(session)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
