"""Caricamento configurazione e secrets.

In locale le chiavi vivono in un file `.env` in root (escluso da git, vedi
`.env.example`); in produzione la stessa interfaccia potrà leggere da
podman secret senza cambiare il chiamante.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env")


def get_api_key(name: str) -> str:
    """Legge una API key dall'ambiente, sollevando un errore esplicito se manca."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Variabile d'ambiente '{name}' non impostata. Copia .env.example in .env "
            "in root del progetto e valorizzala."
        )
    return value


def get_database_url() -> str:
    """Costruisce la connection string SQLAlchemy per Postgres+TimescaleDB.

    Se `DATABASE_URL` è impostata ha precedenza su tutto; altrimenti viene
    composta dalle singole variabili `POSTGRES_*` (con default coerenti col
    quadlet di sviluppo in `deploy/quadlet/marketmind-db.container`).
    """
    explicit_url = os.environ.get("DATABASE_URL")
    if explicit_url:
        return explicit_url

    user = os.environ.get("POSTGRES_USER", "marketmind")
    password = os.environ.get("POSTGRES_PASSWORD", "marketmind")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "marketmind")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"
