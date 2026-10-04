"""Policy di accesso per ruolo applicativo.

Rispecchia lato applicazione i `GRANT` dei ruoli Postgres
(`Market Mind AI - Docs/db/03_utenti_db.md`): `marketmind_ingestion` scrive
`market_data`/`raw`/`audit`, `marketmind_app` scrive `decisions`/`portfolio`.
Postgres resta l'ultima parola sui permessi; la policy serve a fallire
subito, con un messaggio chiaro e prima di toccare il DB, quando un
componente prova a scrivere fuori dal proprio perimetro — anche in
sviluppo, dove ci si connette spesso col superuser che i `GRANT` non li
vede.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Table

from marketmind_db.exceptions import AccessDeniedError


@dataclass(frozen=True)
class AccessPolicy:
    name: str
    writable_schemas: frozenset[str]

    def check_writable(self, table: Table) -> None:
        if table.schema not in self.writable_schemas:
            allowed = ", ".join(sorted(self.writable_schemas)) or "nessuno"
            raise AccessDeniedError(
                f"la policy {self.name!r} non può scrivere su {table.fullname} "
                f"(schema {table.schema!r}); schema scrivibili: {allowed}"
            )


INGESTION = AccessPolicy("ingestion", frozenset({"market_data", "raw", "audit"}))
APP = AccessPolicy("app", frozenset({"decisions", "portfolio"}))
READ_ONLY = AccessPolicy("read-only", frozenset())
# Solo per strumenti di manutenzione e per il codice non ancora migrato al
# repository generico (`get_session()`): il nuovo codice dichiara il
# proprio ruolo.
FULL_ACCESS = AccessPolicy(
    "full", frozenset({"market_data", "raw", "audit", "decisions", "portfolio"})
)
