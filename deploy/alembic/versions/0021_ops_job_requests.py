"""Schema `ops` e `ops.t_job_requests`: la coda dei job chiesti dalla dashboard.

La dashboard, anche in un container senza accesso a systemd, chiede un job
(una pipeline, un giro del Decision Engine, il bootstrap o il backtest di un
portfolio) scrivendo una riga qui; un dispatcher sull'host la prende in
carico e avvia la unit corrispondente (`marketmind_frontend.dispatch`).

I grant sullo schema nuovo sono qui, e non solo in `init-roles.sql.tmpl`:
`deploy.sh` allinea i ruoli prima delle migrazioni, quindi al primo deploy
lo schema non esiste ancora quando gira lo script dei ruoli. Si applicano ai
ruoli applicativi che esistono già (su un DB senza ruoli, come quello usa-e-
getta dei test, non fa nulla); `init-roles.sql.tmpl` li riapplica a ogni
deploy successivo. `marketmind_app` (dashboard e dispatcher) legge e scrive,
`marketmind_readonly` legge.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-12

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEMA = "ops"

_GRANTS = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'marketmind_app') THEN
        GRANT USAGE ON SCHEMA ops TO marketmind_app;
        GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA ops TO marketmind_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT SELECT, INSERT, UPDATE ON TABLES TO marketmind_app;
    END IF;
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'marketmind_readonly') THEN
        GRANT USAGE ON SCHEMA ops TO marketmind_readonly;
        GRANT SELECT ON ALL TABLES IN SCHEMA ops TO marketmind_readonly;
        ALTER DEFAULT PRIVILEGES IN SCHEMA ops GRANT SELECT ON TABLES TO marketmind_readonly;
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS ops")
    op.create_table(
        "t_job_requests",
        sa.Column("job_request_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("requested_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('pipeline', 'decide', 'init-portfolio', 'backtest')", name="kind"),
        sa.CheckConstraint("status IN ('pending', 'started', 'failed')", name="status"),
        sa.CheckConstraint("(kind = 'decide') = (target IS NULL)", name="target_required"),
        schema=_SCHEMA,
    )
    op.create_index("ib_job_requests_status_requested", "t_job_requests", ["status", "requested_at"], schema=_SCHEMA)
    op.execute(_GRANTS)


def downgrade() -> None:
    op.drop_index("ib_job_requests_status_requested", "t_job_requests", schema=_SCHEMA)
    op.drop_table("t_job_requests", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS ops")
