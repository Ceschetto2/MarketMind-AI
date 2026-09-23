"""Merge delle due head Alembic nate dalla stessa base (`0012`) su due
branch indipendenti.

`0013_fix_portfolio_delete_snapshot_trigger` (checkout principale — fix
del trigger `AFTER DELETE` su `t_portfolios`) e `0015_audit_log_run_id_set_null`
(`worktree-llm-decision-engine`, via `0014_portfolio_decision_scheduling` —
cadenza per-portfolio del Decision Engine e fix del FK `t_audit_logs.run_id`)
sono state scritte in parallelo nello stesso punto della storia, senza
sapere l'una dell'altra — vedi `Market Mind AI - Docs/tasks/
2026-09-23-migration-head-collision.md` per la diagnosi completa.

Nessuna DDL propria: entrambe le catene erano già applicate per davvero
sull'istanza Postgres di sviluppo condivisa prima di questo merge (fix del
trigger verificato leggendo la funzione live, colonne/FK di `0014`/`0015`
applicate durante lo sviluppo del timer) — questa migrazione esiste solo
per dare ad Alembic un'unica head da cui proseguire.

Revision ID: 0016
Revises: 0013, 0015
Create Date: 2026-09-23

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, None] = ("0013", "0015")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
