"""Include `source` nella chiave di unicità di `t_company_events` (agenda #52).

Scoperto nel test end-to-end reale di `fmp` su UNH: il vincolo
`(asset_id, ts, event_type)` (0006) non distingue tra fonti diverse — se
Finnhub e FMP scrivono entrambi `event_type='earnings'` per lo stesso asset
alla stessa data, il secondo `write_company_event` sovrascrive
`source`/`raw_payload` del primo invece di conviverci, perdendo
l'attribuzione della fonte più vecchia.

Nuovo vincolo `(asset_id, ts, event_type, source)`: le due fonti convivono
come righe distinte per lo stesso evento. L'upsert idempotente di
`write_company_event` resta invariato nel principio — solo la propria
fonte, riesecuzione della stessa pipeline sulla stessa finestra — ma la
chiave di conflitto si allarga per includere `source`, coerente col nuovo
vincolo di database.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-12

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_t_company_events_asset_id", "t_company_events", schema="market_data", type_="unique"
    )
    op.create_unique_constraint(
        "uq_t_company_events_asset_id",
        "t_company_events",
        ["asset_id", "ts", "event_type", "source"],
        schema="market_data",
    )


def downgrade() -> None:
    # Un downgrade con più righe (asset_id, ts, event_type) da fonti diverse
    # già presenti violerebbe il vincolo più stretto di 0006 — non gestito
    # qui: chi esegue il downgrade deve prima deduplicare a mano se serve,
    # stesso principio delle altre migrazioni di questo repo (nessun
    # downgrade "safe by default" su un vincolo che si stringe).
    op.drop_constraint(
        "uq_t_company_events_asset_id", "t_company_events", schema="market_data", type_="unique"
    )
    op.create_unique_constraint(
        "uq_t_company_events_asset_id",
        "t_company_events",
        ["asset_id", "ts", "event_type"],
        schema="market_data",
    )
