"""Distingue i tre bilanci FMP con `event_type` propri, non tutti `'earnings'`.

`income-statement`, `balance-sheet-statement` e `cash-flow-statement`
condividevano `event_type='earnings'` — se riferiti alla stessa `date`
(probabile, un bilancio deposita i tre statement insieme per lo stesso
periodo), collidevano sulla stessa chiave `(asset_id, ts, event_type,
source)` anche dopo `0007` (che aveva risolto solo la collisione tra
*fonti* diverse, Finnhub vs FMP, non tra endpoint della stessa fonte):
l'upsert scriveva silenziosamente sopra i primi due, perdendo bilancio
patrimoniale e conto economico. Osservato realmente su UNH.

Nuovi valori: `income_statement`, `balance_sheet`, `cash_flow`. `earnings`
resta il valore esclusivo di Finnhub (`/calendar/earnings`) — FMP non lo
scrive più. `dividend`/`split` invariati.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-12

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_VALUES = "'earnings', 'income_statement', 'balance_sheet', 'cash_flow', 'dividend', 'split'"
_OLD_VALUES = "'earnings', 'dividend', 'split'"


def upgrade() -> None:
    op.drop_constraint(
        "event_type", "t_company_events", schema="market_data", type_="check"
    )
    op.create_check_constraint(
        "event_type",
        "t_company_events",
        f"event_type IN ({_NEW_VALUES})",
        schema="market_data",
    )


def downgrade() -> None:
    # Un downgrade con righe già scritte come income_statement/balance_sheet/
    # cash_flow violerebbe il CHECK più stretto di prima — non gestito qui,
    # stesso principio delle altre migrazioni di questo repo: chi esegue il
    # downgrade deve prima riconciliare a mano se servisse davvero.
    op.drop_constraint(
        "event_type", "t_company_events", schema="market_data", type_="check"
    )
    op.create_check_constraint(
        "event_type",
        "t_company_events",
        f"event_type IN ({_OLD_VALUES})",
        schema="market_data",
    )
