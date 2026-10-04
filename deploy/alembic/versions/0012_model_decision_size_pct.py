"""`t_model_decisions` guadagna `size_pct`, la size del trade proposta dall'LLM.

Un `BUY`/`SELL` viene eseguito per davvero (`db/portfolio_writer.py`,
`execute_trade()`) con una size (`Decision.size_pct`) proposta dall'LLM
stesso, non una regola deterministica a valle. Prima di questa migrazione
quel valore veniva usato per eseguire il trade ma mai persistito — una
lacuna di audit rispetto a `confidence`/`reasoning`, già salvati.

Nullable: `HOLD` non ha una size (vietata da `Decision`), e le 4 righe
`BUY` già scritte prima di questa migrazione (il primo giro di decisioni
reali di `gemma-growth-test`, prima che `size_pct` esistesse come colonna)
restano con `size_pct=NULL` — nessun modo di recuperare il valore usato
allora, non era mai stato persistito.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-14

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_model_decisions",
        sa.Column("size_pct", sa.Double(), nullable=True),
        schema="decisions",
    )


def downgrade() -> None:
    op.drop_column("t_model_decisions", "size_pct", schema="decisions")
