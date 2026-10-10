"""`t_model_runs` guadagna l'esito del run: `status`, `finished_at`,
`error_message`.

Senza, un run del motore decisionale con tutte le decisioni fallite (modello
non disponibile, quota LLM esaurita) era indistinguibile da uno riuscito, e
faceva comunque avanzare `t_portfolios.next_decision_at` di una settimana.
Stessa forma di `audit.t_ingestion_runs` per le pipeline: `running` alla
creazione, poi `success` (nessuna decisione fallita), `partial` (almeno una,
fino al 50%) o `failed` (oltre il 50%, o errore fuori dalle singole
decisioni) alla chiusura.

`status` è nullable solo per i run precedenti a questa migrazione: il loro
esito reale non è ricostruibile (la watchlist di allora non è nota, quindi
nemmeno quante decisioni mancavano), e valorizzarlo a posteriori
produrrebbe dati falsi. Ogni run creato dopo questa migrazione ha sempre
uno stato.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-05

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEMA = "decisions"
_TABLE = "t_model_runs"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("status", sa.String(20), nullable=True), schema=_SCHEMA)
    op.add_column(
        _TABLE,
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(_TABLE, sa.Column("error_message", sa.Text(), nullable=True), schema=_SCHEMA)
    op.create_check_constraint(
        "status",
        _TABLE,
        "status IN ('running', 'success', 'partial', 'failed')",
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint("status", _TABLE, schema=_SCHEMA, type_="check")
    op.drop_column(_TABLE, "error_message", schema=_SCHEMA)
    op.drop_column(_TABLE, "finished_at", schema=_SCHEMA)
    op.drop_column(_TABLE, "status", schema=_SCHEMA)
