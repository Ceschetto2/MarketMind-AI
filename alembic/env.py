from __future__ import annotations

import logging

from alembic import context
from sqlalchemy import engine_from_config, pool

from marketmind_ai.config import get_database_url
from marketmind_ai.db.base import Base
from marketmind_ai.db import models  # noqa: F401  (popola Base.metadata)

# Oggetto di configurazione Alembic: script_location/prepend_sys_path/ecc.
# vengono da [tool.alembic] in pyproject.toml (non esiste più alembic.ini —
# supporto nativo da Alembic 1.16, vedi commento in pyproject.toml).
config = context.config

# L'URL reale (da .env / POSTGRES_*) non vive in nessun file di config,
# statico o meno: viene sempre calcolato qui, a runtime.
config.set_main_option("sqlalchemy.url", get_database_url())

# Logging: equivalente diretto in Python di quello che prima viveva nelle
# sezioni [loggers]/[handlers]/[formatters] di alembic.ini — non usiamo più
# `logging.config.fileConfig()` perché richiede un file in formato
# ConfigParser (ini), che qui non esiste.
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)-5.5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("alembic").setLevel(logging.INFO)

# Metadata target per l'autogenerate.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Genera SQL senza connettersi al database (`alembic upgrade --sql`)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema=None,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Applica le migrazioni connettendosi direttamente al database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema=None,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
