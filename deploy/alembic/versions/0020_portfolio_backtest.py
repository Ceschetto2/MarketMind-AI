"""`t_backtest_results` passa da un backtest per run a uno per portfolio su
un periodo.

Il Backtesting Engine analizza ora l'intero storico di un portfolio
(`backtest.engine.Backtester`), non i trade di un singolo ciclo: un
risultato appartiene a un portfolio (`portfolio_id`, NOT NULL), e `run_id`
diventa nullable, valorizzato solo per i risultati del vecchio backtest per
run (proof of concept). Nuove colonne: `total_return` (rendimento sul
periodo), `benchmark_return` (SPY comprato e tenuto nello stesso periodo) e
`created_at`; le prime due nullable perché i risultati precedenti non le
hanno.

Il downgrade cancella i risultati senza `run_id` (non rappresentabili nello
schema di prima) e ripristina il vincolo.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-11

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEMA = "decisions"
_TABLE = "t_backtest_results"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("portfolio_id", sa.BigInteger(), nullable=True), schema=_SCHEMA)
    op.execute(
        "UPDATE decisions.t_backtest_results b SET portfolio_id = r.portfolio_id "
        "FROM decisions.t_model_runs r WHERE r.run_id = b.run_id"
    )
    op.alter_column(_TABLE, "portfolio_id", nullable=False, schema=_SCHEMA)
    op.create_foreign_key(
        "fk_t_backtest_results_portfolio_id_t_portfolios",
        _TABLE, "t_portfolios", ["portfolio_id"], ["portfolio_id"],
        source_schema=_SCHEMA, referent_schema="portfolio",
    )
    op.create_index("ib_backtest_results_portfolio_id", _TABLE, ["portfolio_id"], schema=_SCHEMA)
    op.alter_column(_TABLE, "run_id", nullable=True, schema=_SCHEMA)
    op.add_column(_TABLE, sa.Column("total_return", sa.Double(), nullable=True), schema=_SCHEMA)
    op.add_column(_TABLE, sa.Column("benchmark_return", sa.Double(), nullable=True), schema=_SCHEMA)
    op.add_column(
        _TABLE,
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.execute("DELETE FROM decisions.t_backtest_results WHERE run_id IS NULL")
    op.drop_column(_TABLE, "created_at", schema=_SCHEMA)
    op.drop_column(_TABLE, "benchmark_return", schema=_SCHEMA)
    op.drop_column(_TABLE, "total_return", schema=_SCHEMA)
    op.alter_column(_TABLE, "run_id", nullable=False, schema=_SCHEMA)
    op.drop_index("ib_backtest_results_portfolio_id", _TABLE, schema=_SCHEMA)
    op.drop_constraint("fk_t_backtest_results_portfolio_id_t_portfolios", _TABLE, schema=_SCHEMA, type_="foreignkey")
    op.drop_column(_TABLE, "portfolio_id", schema=_SCHEMA)
