"""`equity_value` esce da `t_portfolios` e da `t_portfolio_snapshots`.

La colonna doveva contenere il valore di mercato del portfolio, ma nessun
codice l'ha mai ricalcolata: restava ferma al capitale iniziale, un dato
fuorviante per chiunque la leggesse. Il valore di mercato si calcola da
cash, posizioni e prezzi (la dashboard lo fa già, il backtest lo farà sullo
stesso storico), quindi non serve una seconda copia nel DB da tenere
allineata.

`fn_portfolio_snapshot()` va riscritta prima di togliere le colonne, perché
le referenzia: stesso comportamento della versione di `0013` (nessuna riga
di storico sul DELETE di un portfolio), senza `equity_value`.

Il downgrade rimette le colonne valorizzate col capitale iniziale del
portfolio, cioè esattamente il valore che avevano prima (mai aggiornato).

Non retrocompatibile con il codice precedente, che mappa ancora la colonna
nell'ORM: va applicata insieme alle immagini nuove (`deploy.sh --build`,
che costruisce le immagini prima di eseguire le migrazioni).

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-11

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SCHEMA = "portfolio"

_FUNCTION_WITHOUT_EQUITY = """
    CREATE OR REPLACE FUNCTION portfolio.fn_portfolio_snapshot() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;

        INSERT INTO portfolio.t_portfolio_snapshots
            (portfolio_id, run_id, cash, operation, changed_at)
        VALUES (
            NEW.portfolio_id,
            NULLIF(current_setting('marketmind.model_run_id', true), '')::bigint,
            NEW.cash,
            TG_OP,
            now()
        );
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
"""

# Versione di `0013`, ripristinata dal downgrade.
_FUNCTION_WITH_EQUITY = """
    CREATE OR REPLACE FUNCTION portfolio.fn_portfolio_snapshot() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        END IF;

        INSERT INTO portfolio.t_portfolio_snapshots
            (portfolio_id, run_id, cash, equity_value, operation, changed_at)
        VALUES (
            NEW.portfolio_id,
            NULLIF(current_setting('marketmind.model_run_id', true), '')::bigint,
            NEW.cash,
            NEW.equity_value,
            TG_OP,
            now()
        );
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(_FUNCTION_WITHOUT_EQUITY)
    op.drop_column("t_portfolio_snapshots", "equity_value", schema=_SCHEMA)
    op.drop_column("t_portfolios", "equity_value", schema=_SCHEMA)


def downgrade() -> None:
    op.add_column("t_portfolios", sa.Column("equity_value", sa.Double(), nullable=True), schema=_SCHEMA)
    op.add_column("t_portfolio_snapshots", sa.Column("equity_value", sa.Double(), nullable=True), schema=_SCHEMA)
    # UPDATE sulla colonna nuova: il trigger scatterebbe e inserirebbe uno
    # snapshot per ogni portfolio, quindi va disattivato per la durata del
    # backfill.
    op.execute("ALTER TABLE portfolio.t_portfolios DISABLE TRIGGER trg_portfolio_snapshot")
    op.execute("UPDATE portfolio.t_portfolios SET equity_value = starting_capital")
    op.execute("ALTER TABLE portfolio.t_portfolios ENABLE TRIGGER trg_portfolio_snapshot")
    op.execute(
        "UPDATE portfolio.t_portfolio_snapshots s SET equity_value = p.starting_capital "
        "FROM portfolio.t_portfolios p WHERE p.portfolio_id = s.portfolio_id"
    )
    op.alter_column("t_portfolios", "equity_value", nullable=False, schema=_SCHEMA)
    op.alter_column("t_portfolio_snapshots", "equity_value", nullable=False, schema=_SCHEMA)
    op.execute(_FUNCTION_WITH_EQUITY)
