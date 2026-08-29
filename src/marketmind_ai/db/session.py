"""Engine e session factory SQLAlchemy per Postgres+TimescaleDB.

L'engine è creato lazy (alla prima chiamata a `get_engine()`) così che
importare questo modulo non richieda una connessione al database né la
presenza di `.env` — utile per test unitari e per gli script di ingestion
che validano solo i record Pydantic senza scrivere su Postgres.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from marketmind_ai.config import get_database_url


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(get_database_url(), pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


@contextmanager
def get_session() -> Iterator[Session]:
    """Context manager con commit/rollback automatico.

    Uso tipico::

        with get_session() as session:
            session.add(obj)
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
