"""`t_model_decisions` guadagna `recheck_at` e `recheck_reason`: il rinvio
chiesto dall'LLM.

Con una decisione il modello può chiedere di rivalutare l'asset fra qualche
ora, dentro il ciclo in corso (`llm.schemas.DeferralRequest`). Il motore
salva qui quando e perché, e il giro orario `run-due` riprende gli asset la
cui ultima decisione nel run ha un ricontrollo scaduto: stesso schema di
`t_portfolios.next_decision_at`, la cadenza vive nel DB e non in un timer,
quindi funziona uguale col deploy Quadlet e con quello Docker/cron.

Due colonne nullable, nessun backfill: le decisioni precedenti non hanno
ricontrolli. Retrocompatibile col codice precedente, che non le conosce.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-11

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEMA = "decisions"
_TABLE = "t_model_decisions"


def upgrade() -> None:
    op.add_column(
        _TABLE, sa.Column("recheck_at", postgresql.TIMESTAMP(timezone=True), nullable=True), schema=_SCHEMA
    )
    op.add_column(_TABLE, sa.Column("recheck_reason", sa.Text(), nullable=True), schema=_SCHEMA)


def downgrade() -> None:
    op.drop_column(_TABLE, "recheck_reason", schema=_SCHEMA)
    op.drop_column(_TABLE, "recheck_at", schema=_SCHEMA)
