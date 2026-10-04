"""Connessione a Postgres+TimescaleDB: `Database`, `DatabaseSettings`, `Transaction`.

Un processo costruisce un `Database` dichiarando il proprio ruolo
(`AccessPolicy`): un container di ingestion `INGESTION`, il Decision Engine
`APP`, la dashboard `READ_ONLY`. L'engine è creato solo alla prima
connessione, così importare il pacchetto non richiede né il DB né `.env`.

`transaction()` è il punto d'ingresso del codice nuovo: restituisce una
`Transaction` da cui ottenere un `TableRepository` per tabella, tutti sulla
stessa sessione. `session()` resta disponibile per il codice non ancora
migrato al repository, ma salta i safeguard di `TableRepository`.

A ogni sessione vengono impostati, come primi statement, le GUC del
`run_id` tracciato (`run_context`) e uno `statement_timeout` locale alla
transazione: una query bloccata (lock, piano sbagliato) fallisce invece di
tenere fermo il container a tempo indefinito.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any

from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from marketmind_common.config import get_database_url
from marketmind_db.access import FULL_ACCESS, AccessPolicy
from marketmind_db.base import Base
from marketmind_db.repository import TableRepository
from marketmind_db.run_context import apply_run_context, set_local_setting

DEFAULT_STATEMENT_TIMEOUT_MS = 60_000


@dataclass(frozen=True)
class DatabaseSettings:
    # `repr=False`: l'URL contiene la password, non deve finire nei log.
    url: str = field(repr=False)
    statement_timeout_ms: int | None = DEFAULT_STATEMENT_TIMEOUT_MS
    pool_pre_ping: bool = True

    DEFAULT_STATEMENT_TIMEOUT_MS = DEFAULT_STATEMENT_TIMEOUT_MS

    @classmethod
    def from_env(cls, **overrides: Any) -> DatabaseSettings:
        """URL da `DATABASE_URL`/`POSTGRES_*` (`marketmind_common.config`)."""
        return replace(cls(url=get_database_url()), **overrides)


class Transaction:
    """Una transazione aperta: dà un `TableRepository` per tabella, tutti
    sulla stessa sessione e con la policy del `Database` che l'ha aperta."""

    def __init__(self, session: Session, policy: AccessPolicy) -> None:
        self._session = session
        self.policy = policy
        self._repositories: dict[type[Base], TableRepository[Any]] = {}

    def repository[ModelT: Base](self, model: type[ModelT]) -> TableRepository[ModelT]:
        if model not in self._repositories:
            self._repositories[model] = TableRepository(self._session, model, self.policy)
        return self._repositories[model]


class Database:
    def __init__(
        self,
        settings: DatabaseSettings,
        *,
        policy: AccessPolicy,
        dry_run: bool = False,
        bind: Engine | Connection | None = None,
    ) -> None:
        """`bind` sostituisce l'engine creato da `settings.url` — usato nei
        test per legare tutto a una connessione con transazione esterna
        annullata a fine test. `dry_run=True` annulla ogni transazione
        invece di committarla."""
        self.settings = settings
        self.policy = policy
        self.dry_run = dry_run
        self._bind = bind
        self._factory: sessionmaker[Session] | None = None

    @property
    def bind(self) -> Engine | Connection:
        if self._bind is None:
            self._bind = create_engine(
                self.settings.url, pool_pre_ping=self.settings.pool_pre_ping, future=True
            )
        return self._bind

    @property
    def engine(self) -> Engine:
        bind = self.bind
        return bind.engine if isinstance(bind, Connection) else bind

    def session_factory(self) -> sessionmaker[Session]:
        if self._factory is None:
            self._factory = sessionmaker(
                bind=self.bind,
                autoflush=False,
                expire_on_commit=False,
                # Con `bind` una connessione già in transazione, ogni commit
                # diventa un SAVEPOINT invece di chiudere quella esterna.
                join_transaction_mode="create_savepoint",
            )
        return self._factory

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Sessione con commit automatico (rollback su eccezione o in dry run)."""
        session = self.session_factory()()
        try:
            apply_run_context(session)
            if self.settings.statement_timeout_ms is not None:
                set_local_setting(session, "statement_timeout", self.settings.statement_timeout_ms)
            yield session
            if self.dry_run:
                session.rollback()
            else:
                session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def transaction(self) -> Iterator[Transaction]:
        with self.session() as session:
            yield Transaction(session, self.policy)


@lru_cache(maxsize=1)
def default_database() -> Database:
    """Il `Database` condiviso dal codice non ancora migrato (`get_session()`,
    `writer.py`, Decision Engine, dashboard): accesso completo e nessuno
    `statement_timeout`, cioè lo stesso comportamento di prima del
    repository generico. Il codice nuovo costruisce il proprio `Database`
    con la policy del suo ruolo."""
    return Database(DatabaseSettings.from_env(statement_timeout_ms=None), policy=FULL_ACCESS)
