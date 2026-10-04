"""Base dichiarativa SQLAlchemy condivisa da tutti i modelli ORM.

La naming convention esplicita rende deterministici i nomi di indici e
vincoli generati da Alembic (`ix_...`, `uq_...`, `fk_...`, ...), invece di
lasciare che Postgres assegni nomi impliciti difficili da referenziare nelle
migrazioni successive.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base dichiarativa per tutti i modelli ORM del progetto."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
