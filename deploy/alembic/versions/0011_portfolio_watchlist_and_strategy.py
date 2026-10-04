"""Bootstrap del portfolio: strategia persistente e watchlist di asset.

Un portfolio 'model' viene attivato in due passi: un bootstrap (una singola
chiamata LLM, non `decide()`) che sceglie quali asset dell'universo
osservare, dato l'universo intero e la strategia del portfolio; poi il loop
settimanale ordinario (`decide()` per ogni asset) gira solo su quello
scope, non più sull'intero universo condiviso da tutti i portfolio.

`t_portfolios` guadagna `strategy_prompt` (testo libero, persistente — non
solo per il bootstrap: resta nel context package di ogni decisione futura
di quel portfolio, la strategia guida ogni giudizio, non solo la selezione
iniziale). Il CHECK che impone le impostazioni di un portfolio 'model' si
estende per includerlo, sostituendo quello di `0010`.

Nuova tabella `portfolio.t_portfolio_watchlist`: quali asset questo
portfolio osserva, popolata dal bootstrap. Distinta da
`t_portfolio_positions` (quantità/prezzo di carico, capitale impegnato):
la watchlist non impegna capitale, è solo lo scope decisionale — impegnare
capitale resta responsabilità del Backtesting Engine, non ancora scritto.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-13

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_portfolios",
        sa.Column("strategy_prompt", sa.Text(), nullable=True),
        schema="portfolio",
    )

    # gemini-baseline esiste già (creato prima di questa migrazione, senza
    # strategy_prompt): il nuovo CHECK fallirebbe contro quella riga reale
    # se non la si backfilla prima di crearlo.
    op.execute(
        "UPDATE portfolio.t_portfolios "
        "SET strategy_prompt = 'Strategia non ancora definita: valuta gli asset "
        "dell''universo sui soli segnali di mercato disponibili, senza una linea "
        "guida specifica.' "
        "WHERE portfolio_type = 'model' AND strategy_prompt IS NULL"
    )

    op.drop_constraint(
        "llm_settings_required_for_model", "t_portfolios", schema="portfolio", type_="check"
    )
    op.create_check_constraint(
        "llm_settings_required_for_model",
        "t_portfolios",
        "portfolio_type <> 'model' OR "
        "(llm_provider IS NOT NULL AND model_version IS NOT NULL AND strategy_prompt IS NOT NULL)",
        schema="portfolio",
    )

    op.create_table(
        "t_portfolio_watchlist",
        sa.Column(
            "portfolio_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "portfolio.t_portfolios.portfolio_id",
                name="fk_t_portfolio_watchlist_portfolio_id_t_portfolios",
            ),
            primary_key=True,
        ),
        sa.Column(
            "asset_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "market_data.t_assets.asset_id",
                name="fk_t_portfolio_watchlist_asset_id_t_assets",
            ),
            primary_key=True,
        ),
        sa.Column("added_at", TIMESTAMP(timezone=True), nullable=False),
        schema="portfolio",
    )


def downgrade() -> None:
    op.drop_table("t_portfolio_watchlist", schema="portfolio")

    op.drop_constraint(
        "llm_settings_required_for_model", "t_portfolios", schema="portfolio", type_="check"
    )
    op.create_check_constraint(
        "llm_settings_required_for_model",
        "t_portfolios",
        "portfolio_type <> 'model' OR (llm_provider IS NOT NULL AND model_version IS NOT NULL)",
        schema="portfolio",
    )
    op.drop_column("t_portfolios", "strategy_prompt", schema="portfolio")
