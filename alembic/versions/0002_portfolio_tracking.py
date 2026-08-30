"""Tracking del portafoglio virtuale: 4 tabelle in un proprio schema `portfolio`, trigger dedicati.

Rispecchia colonna per colonna l'`erDiagram` confermato in
`Market Mind AI - Docs/Architettura/01_schema_dati_er.md` (rework del
29-08-26, agenda #33/#34, e refactor di schema del 30-08-26, agenda #35):
`t_portfolios` (contenitore generico, `portfolio_type` 'model' | 'benchmark',
stato corrente mutabile cash/equity_value) e `t_portfolio_positions` (stato
corrente mutabile per asset), più `t_portfolio_snapshots`/
`t_portfolio_position_snapshots` (storico append-only). SPY buy & hold entra
semplicemente come un'altra riga di `t_portfolios`, non come tabella o
colonna dedicata.

Le quattro tabelle vivono in uno schema Postgres dedicato, `portfolio`, non
in `decisions`: il portafoglio è lo stato applicativo che *risulta*
dall'eseguire le decisioni nel tempo, non una decisione in sé — un dominio
a sé rispetto a `t_model_runs`/`t_model_decisions`/`t_backtest_results`,
con un proprio ciclo di vita (mutabile + storico) che non condivide nulla
strutturalmente con quelle tre tabelle. La separazione applica lo stesso
criterio di confine per area applicativa già usato per gli altri schema
(permessi differenziati per ruolo, retention distinta).

A differenza di `t_audit_logs` (log generico con `diff` in JSONB,
`audit.fn_audit_log()`), qui servono due trigger dedicati e tipizzati —
`portfolio.fn_portfolio_snapshot()` / `portfolio.fn_portfolio_position_snapshot()`:
un'equity curve richiede sommare `quantity`/`cash`/`equity_value`
direttamente in SQL, non estrarli da JSONB. Per questo
`t_portfolios`/`t_portfolio_positions` restano fuori da `AUDITED_TABLES`
(0001) — nessun doppio logging dello stesso evento.

Il `run_id` di ogni snapshot è nullable (non ogni cambio di cash/posizione
origina da un run tracciato) e viene letto da una GUC di sessione,
`marketmind.model_run_id`, con lo stesso meccanismo già usato da
`audit.fn_audit_log()` per `marketmind.ingestion_run_id`: lo strato di
scrittura in `db/` la imposta con `SET LOCAL` prima di scrivere durante un
run del decision engine tracciato, e resta NULL altrimenti. La FK verso
`decisions.t_model_runs.run_id` è cross-schema, supportata nativamente da
Postgres.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-30

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS portfolio")

    # --- portfolio.t_portfolios ---
    op.create_table(
        "t_portfolios",
        sa.Column("portfolio_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("portfolio_type", sa.String(20), nullable=False),
        sa.Column("starting_capital", sa.Double(), nullable=False),
        sa.Column("cash", sa.Double(), nullable=False),
        sa.Column("equity_value", sa.Double(), nullable=False),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
        sa.CheckConstraint(
            "portfolio_type IN ('model', 'benchmark')", name="portfolio_type"
        ),
        schema="portfolio",
    )

    # --- portfolio.t_portfolio_positions ---
    op.create_table(
        "t_portfolio_positions",
        sa.Column(
            "portfolio_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "portfolio.t_portfolios.portfolio_id",
                name="fk_t_portfolio_positions_portfolio_id_t_portfolios",
            ),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "market_data.t_assets.asset_id",
                name="fk_t_portfolio_positions_asset_id_t_assets",
            ),
            nullable=False,
        ),
        sa.Column("quantity", sa.Double(), nullable=False),
        sa.Column("avg_price", sa.Double(), nullable=False),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "portfolio_id", "asset_id", name="pk_t_portfolio_positions"
        ),
        schema="portfolio",
    )

    # --- portfolio.t_portfolio_snapshots ---
    op.create_table(
        "t_portfolio_snapshots",
        sa.Column("snapshot_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "portfolio_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "portfolio.t_portfolios.portfolio_id",
                name="fk_t_portfolio_snapshots_portfolio_id_t_portfolios",
            ),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "decisions.t_model_runs.run_id",
                name="fk_t_portfolio_snapshots_run_id_t_model_runs",
            ),
        ),
        sa.Column("cash", sa.Double(), nullable=False),
        sa.Column("equity_value", sa.Double(), nullable=False),
        sa.Column("operation", sa.String(10), nullable=False),
        sa.Column(
            "changed_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "operation IN ('INSERT', 'UPDATE', 'DELETE')", name="operation"
        ),
        schema="portfolio",
    )
    op.create_index(
        "ib_portfolio_snapshots_portfolio_changed",
        "t_portfolio_snapshots",
        ["portfolio_id", "changed_at"],
        schema="portfolio",
    )
    op.create_index(
        "ib_portfolio_snapshots_run_id",
        "t_portfolio_snapshots",
        ["run_id"],
        schema="portfolio",
    )

    # --- portfolio.t_portfolio_position_snapshots ---
    op.create_table(
        "t_portfolio_position_snapshots",
        sa.Column("snapshot_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "portfolio_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "portfolio.t_portfolios.portfolio_id",
                name="fk_t_portfolio_position_snapshots_portfolio_id_t_portfolios",
            ),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "market_data.t_assets.asset_id",
                name="fk_t_portfolio_position_snapshots_asset_id_t_assets",
            ),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "decisions.t_model_runs.run_id",
                name="fk_t_portfolio_position_snapshots_run_id_t_model_runs",
            ),
        ),
        sa.Column("quantity", sa.Double(), nullable=False),
        sa.Column("avg_price", sa.Double(), nullable=False),
        sa.Column("operation", sa.String(10), nullable=False),
        sa.Column(
            "changed_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "operation IN ('INSERT', 'UPDATE', 'DELETE')", name="operation"
        ),
        schema="portfolio",
    )
    op.create_index(
        "ib_portfolio_position_snapshots_portfolio_asset_changed",
        "t_portfolio_position_snapshots",
        ["portfolio_id", "asset_id", "changed_at"],
        schema="portfolio",
    )
    op.create_index(
        "ib_portfolio_position_snapshots_run_id",
        "t_portfolio_position_snapshots",
        ["run_id"],
        schema="portfolio",
    )

    # --- trigger dedicato: t_portfolios -> t_portfolio_snapshots ---
    op.execute(
        """
        CREATE FUNCTION portfolio.fn_portfolio_snapshot() RETURNS trigger AS $$
        DECLARE
            v_row portfolio.t_portfolios%ROWTYPE;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                v_row := OLD;
            ELSE
                v_row := NEW;
            END IF;

            INSERT INTO portfolio.t_portfolio_snapshots
                (portfolio_id, run_id, cash, equity_value, operation, changed_at)
            VALUES (
                v_row.portfolio_id,
                NULLIF(current_setting('marketmind.model_run_id', true), '')::bigint,
                v_row.cash,
                v_row.equity_value,
                TG_OP,
                now()
            );
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_portfolio_snapshot
        AFTER INSERT OR UPDATE OR DELETE ON portfolio.t_portfolios
        FOR EACH ROW EXECUTE FUNCTION portfolio.fn_portfolio_snapshot()
        """
    )

    # --- trigger dedicato: t_portfolio_positions -> t_portfolio_position_snapshots ---
    op.execute(
        """
        CREATE FUNCTION portfolio.fn_portfolio_position_snapshot() RETURNS trigger AS $$
        DECLARE
            v_row portfolio.t_portfolio_positions%ROWTYPE;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                v_row := OLD;
            ELSE
                v_row := NEW;
            END IF;

            INSERT INTO portfolio.t_portfolio_position_snapshots
                (portfolio_id, asset_id, run_id, quantity, avg_price, operation, changed_at)
            VALUES (
                v_row.portfolio_id,
                v_row.asset_id,
                NULLIF(current_setting('marketmind.model_run_id', true), '')::bigint,
                v_row.quantity,
                v_row.avg_price,
                TG_OP,
                now()
            );
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_portfolio_position_snapshot
        AFTER INSERT OR UPDATE OR DELETE ON portfolio.t_portfolio_positions
        FOR EACH ROW EXECUTE FUNCTION portfolio.fn_portfolio_position_snapshot()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_portfolio_position_snapshot ON portfolio.t_portfolio_positions"
    )
    op.execute("DROP FUNCTION IF EXISTS portfolio.fn_portfolio_position_snapshot()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_portfolio_snapshot ON portfolio.t_portfolios"
    )
    op.execute("DROP FUNCTION IF EXISTS portfolio.fn_portfolio_snapshot()")

    op.drop_table("t_portfolio_position_snapshots", schema="portfolio")
    op.drop_table("t_portfolio_snapshots", schema="portfolio")
    op.drop_table("t_portfolio_positions", schema="portfolio")
    op.drop_table("t_portfolios", schema="portfolio")

    op.execute("DROP SCHEMA IF EXISTS portfolio CASCADE")
