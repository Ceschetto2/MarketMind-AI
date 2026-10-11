"""Caricamento configurazione e secrets.

In locale le chiavi vivono in un file `.env` in root (escluso da git, vedi
`.env.example`). Nei container ogni variabile può arrivare anche da un file,
indicato da `<NOME>_FILE` (es. `POSTGRES_PASSWORD_FILE`), con precedenza sulla
variabile semplice: così un secret montato da un volume Docker non passa mai
dall'ambiente del container, visibile con `docker inspect`.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

# packages/marketmind_common/src/config.py -> root del repo.
# Installato come wheel in un'immagine (site-packages) il percorso non porta
# a nessun `.env`, e `load_dotenv` non fa nulla: lì le variabili arrivano
# dall'ambiente del container o dai file `<NOME>_FILE`.
_REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_REPO_ROOT / ".env")


def _read_setting(name: str, default: str | None = None) -> str | None:
    """Valore di `name`: dal file in `<name>_FILE` se impostata, altrimenti dall'ambiente."""
    file_path = os.environ.get(f"{name}_FILE")
    if file_path:
        try:
            return Path(file_path).read_text().rstrip("\r\n")
        except OSError as exc:
            raise RuntimeError(f"Impossibile leggere {name}_FILE ({file_path}): {exc}") from exc
    return os.environ.get(name, default)


def get_api_key(name: str) -> str:
    """Legge una API key dall'ambiente, sollevando un errore esplicito se manca."""
    value = _read_setting(name)
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
    password = _read_setting("POSTGRES_PASSWORD", "marketmind")
    # "localhost" risolve anche su ::1 (IPv6): il quadlet di sviluppo
    # pubblica solo su IPv4, quindi con "localhost" un tentativo di
    # connessione può restare appeso sul lato IPv6 fino al timeout prima
    # di arrivare a quello IPv4 che funziona — default a un IP esplicito.
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "marketmind")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db}"


def get_role_database_url(user: str, password_setting: str) -> str | None:
    """Connection string per un ruolo Postgres applicativo diverso da
    `POSTGRES_USER` (es. `marketmind_readonly` per la dashboard): stessi
    `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_DB`, password letta da
    `password_setting` (o dal file in `<password_setting>_FILE`).

    `None` se la password del ruolo non è configurata: è il chiamante a
    decidere se ripiegare su `get_database_url()`. `DATABASE_URL` non è
    considerata, contiene già le credenziali di un altro ruolo.
    """
    password = _read_setting(password_setting)
    if not password:
        return None
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "marketmind")
    return f"postgresql+psycopg://{user}:{quote(password, safe='')}@{host}:{port}/{db}"
