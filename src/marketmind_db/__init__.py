"""Layer di accesso al database (SQLAlchemy + Alembic).

Espone `Base` (per Alembic/`create_all` nei test) e `get_session()` (per il
codice applicativo). I modelli vivono in `marketmind_ai.db.models` e vanno
importati esplicitamente da chi ne ha bisogno (o transitivamente via questo
modulo) perché `Base.metadata` sia popolata.
"""

from __future__ import annotations

from marketmind_db import models  # noqa: F401  (popola Base.metadata)
from marketmind_db.base import Base
from marketmind_db.session import get_engine, get_session, get_session_factory

__all__ = ["Base", "get_engine", "get_session", "get_session_factory", "models"]
