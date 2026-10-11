"""I due `Database` della dashboard, uno per ruolo.

Le letture passano da `marketmind_readonly` (solo `SELECT`, policy
`READ_ONLY`): la dashboard tipicamente raggiunge il DB via tunnel, e un ruolo
che non può scrivere limita il danno di un errore o di credenziali rubate.
Le poche scritture di gestione (`admin`) passano da `marketmind_app`, lo
stesso ruolo del Decision Engine, con la policy `APP`.

Se la password di un ruolo non è configurata (`MARKETMIND_READONLY_PASSWORD`,
`MARKETMIND_APP_PASSWORD`, anche come `<NOME>_FILE`) si ripiega sulle
credenziali di `POSTGRES_USER`/`DATABASE_URL`, con la stessa policy
applicativa: la pagina lo segnala invece di nasconderlo.
"""

from __future__ import annotations

from dataclasses import dataclass

from marketmind_common.config import get_database_url, get_role_database_url
from marketmind_db.access import APP, READ_ONLY, AccessPolicy
from marketmind_db.database import Database, DatabaseSettings

READONLY_ROLE = ("marketmind_readonly", "MARKETMIND_READONLY_PASSWORD")
APP_ROLE = ("marketmind_app", "MARKETMIND_APP_PASSWORD")


@dataclass(frozen=True)
class RoleDatabase:
    database: Database
    role: str
    fallback: bool


@dataclass(frozen=True)
class DashboardDatabases:
    reader: RoleDatabase
    admin: RoleDatabase


def _role_database(role: tuple[str, str], policy: AccessPolicy) -> RoleDatabase:
    user, password_setting = role
    url = get_role_database_url(user, password_setting)
    fallback = url is None
    settings = DatabaseSettings(url=get_database_url() if url is None else url)
    return RoleDatabase(Database(settings, policy=policy), user, fallback)


def dashboard_databases() -> DashboardDatabases:
    return DashboardDatabases(
        reader=_role_database(READONLY_ROLE, READ_ONLY),
        admin=_role_database(APP_ROLE, APP),
    )
