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
