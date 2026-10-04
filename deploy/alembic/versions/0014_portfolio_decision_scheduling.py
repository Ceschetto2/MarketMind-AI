"""`t_portfolios` guadagna `next_decision_at`, la cadenza per-portfolio del
motore decisionale.

Prima di questa migrazione un solo entry point implicito (`run_weekly_
decisions()`, mai invocato da un timer reale) avrebbe girato su tutti i
portfolio 'model' attivi insieme, senza che nessuno di loro sapesse
"quando" deve girare di nuovo. `next_decision_at` è quel "quando": non un
cron fisso lato timer, ma un valore che il motore stesso calcola e
scrive dopo ogni giro di decisioni (default: +7 giorni, coerente con la
cadenza settimanale già decisa) — un timer condiviso, non uno per
portfolio, interroga solo "chi è scaduto ORA" (`get_due_model_portfolios`),
lasciando al motore la libertà di schedulare un prossimo giro diverso dal
default per un singolo portfolio, in futuro anche in risposta a un rinvio
dell'LLM (`Decision.defer`, non ancora collegato a questo campo).

Nullable: un portfolio appena creato (riga inserita, non ancora
`initialize_portfolio()`) non ha ancora un prossimo giro schedulato — NULL
è trattato come "scaduto subito" da `get_due_model_portfolios`, così un
portfolio dimenticato non resta bloccato per sempre in attesa di un valore
che nessuno ha mai scritto.

Nota su questo numero di revisione: nato come `0013` in questo worktree, poi
rinumerato a `0014` perché nel frattempo il checkout principale dell'utente
(non ancora integrato in questo branch) ha aggiunto un proprio `0013`
(`0013_fix_portfolio_delete_snapshot_trigger`, non presente qui) — stesso
`down_revision` (`0012`), due migrazioni indipendenti nate dallo stesso
punto della storia. Segnalato esplicitamente perché non è un dettaglio da
sistemare in silenzio: quando i due branch verranno integrati, Alembic
vedrà due head separate da questo `0012` e servirà una `alembic merge`
esplicita, non solo il rename fatto qui.

Revision ID: 0014
Revises: 0012
Create Date: 2026-09-23

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_portfolios",
        sa.Column("next_decision_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        schema="portfolio",
    )


def downgrade() -> None:
    op.drop_column("t_portfolios", "next_decision_at", schema="portfolio")
