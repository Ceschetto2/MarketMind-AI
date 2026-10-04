"""Layer di accesso al database (SQLAlchemy + Alembic).

Codice nuovo: `Database` (connessione, con la `AccessPolicy` del proprio
ruolo) → `Database.transaction()` → `TableRepository` per tabella, con i
safeguard applicati in un punto solo; `IngestionRunAudit` per l'audit dei
run di ingestion. Codice non ancora migrato: `get_session()`.

Importare il package popola `Base.metadata` con tutti i modelli (per
Alembic e per `create_all` nei test).
"""

from __future__ import annotations

from marketmind_db import models  # noqa: F401  (popola Base.metadata)
from marketmind_db.access import APP, FULL_ACCESS, INGESTION, READ_ONLY, AccessPolicy
from marketmind_db.audit import IngestionRunAudit
from marketmind_db.base import Base
from marketmind_db.database import Database, DatabaseSettings, Transaction, default_database
from marketmind_db.exceptions import AccessDeniedError, InvalidQueryError, RepositoryError
from marketmind_db.repository import TableRepository
from marketmind_db.session import get_engine, get_session, get_session_factory

__all__ = [
    "APP",
    "FULL_ACCESS",
    "INGESTION",
    "READ_ONLY",
    "AccessDeniedError",
    "AccessPolicy",
    "Base",
    "Database",
    "DatabaseSettings",
    "IngestionRunAudit",
    "InvalidQueryError",
    "RepositoryError",
    "TableRepository",
    "Transaction",
    "default_database",
    "get_engine",
    "get_session",
    "get_session_factory",
    "models",
]
