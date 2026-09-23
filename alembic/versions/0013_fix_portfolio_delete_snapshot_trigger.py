"""Fix del trigger di snapshot su `t_portfolios`: un DELETE non tenta più
di fotografare la riga appena cancellata.

Bug noto e documentato da `0002` (`CLAUDE.md` § Ambiguità aperte, verificato
empiricamente): `fn_portfolio_snapshot()` è un trigger `AFTER DELETE` che
prova a inserire una riga di storico in `t_portfolio_snapshots`
riferendosi a `OLD.portfolio_id` — ma a quel punto la riga in `t_portfolios`
è già stata rimossa, quindi la FK `fk_t_portfolio_snapshots_portfolio_id_
t_portfolios` fallisce sempre. Risultato pratico: cancellare un intero
portfolio non è mai stato possibile, in nessuna condizione (non solo in un
caso limite). `fn_portfolio_position_snapshot()`, l'analogo per
`t_portfolio_positions`, non ha lo stesso problema — cancellare una
posizione lascia intatte le righe `t_portfolios`/`t_assets` che referenzia,
quindi non serve toccarla qui.

Fix: per `TG_OP = 'DELETE'`, il trigger non inserisce più nessuna riga di
storico — non c'è un'equity curve da continuare a disegnare per un
portfolio che non esiste più, quindi non c'è nulla di utile da fotografare.
INSERT/UPDATE restano invariati. Emerso lavorando sul Backtesting Engine
(`Market Mind AI - Docs/Backtest/00_motore_backtest.md`): i test di
integrazione del fix al collegamento `run_id` (`Market Mind AI - Docs/
tasks/2026-09-14-run-id-guc-linkage.md`) hanno lasciato un portfolio di
test orfano, cancellabile solo dopo questo fix.

Portata del fix, non equivocare: elimina solo il meccanismo che faceva
fallire *ogni* DELETE, incluso quello di un portfolio senza alcuno storico
pregresso. Non introduce `ON DELETE CASCADE`: un portfolio con snapshot già
esistenti (il caso comune — qualunque INSERT/UPDATE ne crea uno) resta
correttamente non cancellabile finché quello storico non viene rimosso a
sua volta esplicitamente, lo stesso vincolo di integrità referenziale di
qualunque altra FK. Se in futuro servisse una cancellazione a cascata
dell'intero storico di un portfolio, è una scelta di design separata, non
decisa qui.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-14

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_FUNCTION = """
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

_OLD_FUNCTION = """
    CREATE OR REPLACE FUNCTION portfolio.fn_portfolio_snapshot() RETURNS trigger AS $$
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


def upgrade() -> None:
    op.execute(_NEW_FUNCTION)


def downgrade() -> None:
    op.execute(_OLD_FUNCTION)
