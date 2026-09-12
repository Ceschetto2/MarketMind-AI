"""Fixture condivise per i test.

`db_session` lega la sessione a una transazione esterna che viene sempre
annullata a fine test — anche se il codice sotto test chiama
`session.commit()`, grazie a `join_transaction_mode="create_savepoint"`
(SQLAlchemy annida un `SAVEPOINT` per ogni commit del codice testato,
invece di chiudere la transazione esterna). I test di integrazione scrivono
per davvero su Postgres nel corso del test, ma non lasciano nulla dietro di
sé: stesso principio delle verifiche manuali già fatte durante lo sviluppo
(insert di prova + pulizia), solo automatizzato.

Richiede `marketmind-db` raggiungibile (vedi `.env`, `POSTGRES_*`/
`DATABASE_URL`): se il DB non risponde, i test che usano `db_session`
vengono saltati con un messaggio esplicito invece di fallire con un errore
di connessione poco leggibile — utile per poter comunque eseguire i test
`unit` (che non toccano né DB né rete) senza Postgres attivo.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from marketmind_ai.db.session import get_engine


@pytest.fixture(scope="session")
def _db_reachable() -> bool:
    try:
        with get_engine().connect():
            return True
    except OperationalError:
        return False


@pytest.fixture
def db_session(_db_reachable: bool):
    if not _db_reachable:
        pytest.skip("Postgres non raggiungibile (marketmind-db) — vedi .env")

    connection = get_engine().connect()
    transaction = connection.begin()
    session_factory = sessionmaker(
        bind=connection, join_transaction_mode="create_savepoint"
    )
    session: Session = session_factory()

    yield session

    session.close()
    transaction.rollback()
    connection.close()
