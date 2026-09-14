"""Portfolio-aware decisions: settings per portfolio, run legato a un portfolio.

Le decisioni dell'LLM diventano dipendenti dal portfolio per cui vengono
prese — cash e posizioni correnti influenzano la decisione stessa, non solo
cosa farne dopo — e ogni portfolio è isolato dagli altri (provider/modello
propri, nessuna informazione condivisa). Due conseguenze di schema:

`portfolio.t_portfolios` guadagna `is_active` (un portfolio 'model' su cui
il Decision Engine deve girare può essere sospeso senza essere cancellato,
né perdere il proprio storico) e `llm_provider`/`model_version` (impostazioni
proprie di ciascun portfolio, non più solo del run — un portfolio 'model'
può usare un provider diverso da un altro). Le due colonne restano nullable
a livello di tipo, ma un CHECK le impone entrambe non nulle quando
`portfolio_type='model'`: un benchmark (buy & hold, mai una chiamata LLM)
non ne ha bisogno.

`decisions.t_model_runs` guadagna `portfolio_id` (FK NOT NULL): ogni run
appartiene ora a un solo portfolio, mai più un run universale su tutto
l'universo condiviso da chiunque volesse leggerlo. `t_model_decisions` non
guadagna una FK diretta: resta scoped al portfolio transitivamente via
`run_id`, per non duplicare l'informazione.

`t_portfolios` è vuota (nessun portfolio reale creato finora): nessun
backfill necessario lì. `t_model_runs`/`t_model_decisions` hanno invece la
riga di test del 12-09-26 (verifica end-to-end di `llm/`/`decision_engine/`,
`Market Mind AI - Docs/tasks/2026-09-13-decision-engine-e2e-test.md`) — un
run universale, precedente al concetto di portfolio, che non potrebbe
soddisfare il nuovo vincolo NOT NULL. Cancellata come parte di questa
migrazione: era dichiaratamente dato di verifica, non storico da preservare.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-13

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Dato di verifica del 12/13-09-26, precedente al concetto di portfolio
    # — non potrebbe soddisfare il nuovo vincolo NOT NULL su portfolio_id.
    op.execute("DELETE FROM decisions.t_model_decisions")
    op.execute("DELETE FROM decisions.t_model_runs")

    op.add_column(
        "t_portfolios",
        sa.Column("is_active", sa.Boolean(), nullable=False),
        schema="portfolio",
    )
    op.add_column(
        "t_portfolios",
        sa.Column("llm_provider", sa.Text(), nullable=True),
        schema="portfolio",
    )
    op.add_column(
        "t_portfolios",
        sa.Column("model_version", sa.Text(), nullable=True),
        schema="portfolio",
    )
    op.create_check_constraint(
        "llm_settings_required_for_model",
        "t_portfolios",
        "portfolio_type <> 'model' OR (llm_provider IS NOT NULL AND model_version IS NOT NULL)",
        schema="portfolio",
    )

    op.add_column(
        "t_model_runs",
        sa.Column(
            "portfolio_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "portfolio.t_portfolios.portfolio_id",
                name="fk_t_model_runs_portfolio_id_t_portfolios",
            ),
            nullable=False,
        ),
        schema="decisions",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_t_model_runs_portfolio_id_t_portfolios",
        "t_model_runs",
        schema="decisions",
        type_="foreignkey",
    )
    op.drop_column("t_model_runs", "portfolio_id", schema="decisions")

    op.drop_constraint(
        "llm_settings_required_for_model",
        "t_portfolios",
        schema="portfolio",
        type_="check",
    )
    op.drop_column("t_portfolios", "model_version", schema="portfolio")
    op.drop_column("t_portfolios", "llm_provider", schema="portfolio")
    op.drop_column("t_portfolios", "is_active", schema="portfolio")
