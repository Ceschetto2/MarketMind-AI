"""`t_audit_logs.run_id` guadagna `ON DELETE SET NULL` sulla FK verso
`t_ingestion_runs`.

Bug scoperto lavorando sul timer del Decision Engine (23-09-26): la FK
(`fk_t_audit_logs_run_id_t_ingestion_runs`, senza `ON DELETE`) blocca
*qualunque* `DELETE` su `t_ingestion_runs` non appena esiste almeno una
riga di `t_audit_logs` che lo referenzia — cosa che succede quasi sempre,
dato che `t_ingestion_runs` è essa stessa una delle tabelle soggette al
trigger di audit generico. In pratica: nessun run di ingestion si è mai
potuto cancellare per davvero, nemmeno in un contesto di test che pulisce
dietro di sé — non solo in un caso limite, esattamente lo stesso tipo di
bug già corretto per `t_portfolio_snapshots`/`t_portfolios` (agenda,
checkout principale, migrazione indipendente `0013`).

Fix: `run_id` è già nullable per disegno — rappresenta "nessun run noto"
per una modifica non originata da un run tracciato (vedi `db/models/
audit.py`). `ON DELETE SET NULL` è coerente con questa semantica già
esistente: cancellare un run scollega il suo audit log invece di romperne
la cancellazione o, peggio, cancellare a cascata righe di storico che
restano un record legittimo ("questa modifica è avvenuta", anche se il run
che l'ha originata non esiste più).

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-23

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT_NAME = "fk_t_audit_logs_run_id_t_ingestion_runs"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "t_audit_logs", schema="audit", type_="foreignkey")
    op.create_foreign_key(
        _CONSTRAINT_NAME,
        "t_audit_logs",
        "t_ingestion_runs",
        ["run_id"],
        ["run_id"],
        source_schema="audit",
        referent_schema="audit",
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "t_audit_logs", schema="audit", type_="foreignkey")
    op.create_foreign_key(
        _CONSTRAINT_NAME,
        "t_audit_logs",
        "t_ingestion_runs",
        ["run_id"],
        ["run_id"],
        source_schema="audit",
        referent_schema="audit",
    )
