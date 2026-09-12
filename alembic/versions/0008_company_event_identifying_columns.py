"""Colonne identificative dedicate su `t_company_events`.

`Data Providers/05_fmp_onboarding.md` segnalava questi campi come
"candidati a colonne dedicate", dato che sono comuni ai tre bilanci FMP
(income/balance-sheet/cash-flow-statement): `fiscal_year`, `period`,
`reported_currency`, `cik`, `filing_date`, `accepted_date`. L'annotazione
non era mai stata promossa a schema reale — `t_company_events` restava una
tabella puntatore minimale (chiave naturale + `event_type`/`source`/
`fetched_at`), tutta la sostanza del dato solo in `raw.t_company_events_raw.raw_payload`.

Tutte e sei nullable: solo FMP le fornisce. Restano `None` per gli eventi
Finnhub (`/calendar/earnings` non ha un equivalente diretto — il suo
`quarter`/`year` numerico non è la stessa cosa del `period`/`fiscalYear`
testuale di FMP) e per dividendi/split FMP (nessun bilancio associato).

`fiscal_year`/`cik` restano testo, non numerici: coerenti con la
rappresentazione osservata nella risposta grezza FMP (es. `cik` con zeri
iniziali, non un intero). `filing_date`/`accepted_date` come `date`: la
componente oraria eventualmente presente in `acceptedDate` non viene
preservata qui — sono colonne di comodo per query dirette, il dato
completo resta comunque in `raw_payload`.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-12

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "t_company_events", sa.Column("fiscal_year", sa.Text()), schema="market_data"
    )
    op.add_column(
        "t_company_events", sa.Column("period", sa.Text()), schema="market_data"
    )
    op.add_column(
        "t_company_events", sa.Column("reported_currency", sa.Text()), schema="market_data"
    )
    op.add_column(
        "t_company_events", sa.Column("cik", sa.Text()), schema="market_data"
    )
    op.add_column(
        "t_company_events", sa.Column("filing_date", sa.Date()), schema="market_data"
    )
    op.add_column(
        "t_company_events", sa.Column("accepted_date", sa.Date()), schema="market_data"
    )


def downgrade() -> None:
    op.drop_column("t_company_events", "accepted_date", schema="market_data")
    op.drop_column("t_company_events", "filing_date", schema="market_data")
    op.drop_column("t_company_events", "cik", schema="market_data")
    op.drop_column("t_company_events", "reported_currency", schema="market_data")
    op.drop_column("t_company_events", "period", schema="market_data")
    op.drop_column("t_company_events", "fiscal_year", schema="market_data")
