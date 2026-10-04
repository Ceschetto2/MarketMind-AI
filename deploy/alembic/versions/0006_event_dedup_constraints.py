"""Vincoli di unicità naturale su `t_news_events`/`t_company_events`.

Scoperto implementando lo strato di scrittura per le pipeline che alimentano
queste due tabelle (Finnhub, GDELT, FMP): a differenza di `t_market_prices`
(chiave naturale `(asset_id, ts, source)`) e `t_universe_members`
(`asset_id`), le due tabelle eventi non avevano alcun vincolo di unicità
naturale — un upsert idempotente come quello già in uso per le altre due non
era possibile, e ogni riesecuzione su una finestra temporale già coperta
avrebbe duplicato le righe invece di aggiornarle.

`t_news_events`: `url` come chiave naturale — un articolo ha un URL, e due
fetch della stessa fonte sulla stessa finestra temporale restituiscono lo
stesso URL per lo stesso articolo. `t_company_events`: `(asset_id, ts,
event_type)` — un solo evento di un dato tipo per asset per giorno è
un'assunzione ragionevole (earnings/dividend/split non si ripetono più volte
nello stesso giorno per lo stesso titolo nella pratica), non garantita dal
dominio in modo assoluto ma sufficiente come chiave di deduplicazione.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-06

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A differenza di `op.create_check_constraint` (vedi 0004/0005), qui il
    # nome breve non viene espanso dalla naming convention di `Base.metadata`
    # — verificato empiricamente: un primo tentativo con "url"/"asset_id" ha
    # creato vincoli con quei nomi letterali, non `uq_t_..._...`. Nome
    # completo esplicito, in linea con `db/01_schema_dati_er.md` § Naming
    # conventions (`uq_<table>_<column>`, prima colonna per un vincolo
    # composito).
    op.create_unique_constraint(
        "uq_t_news_events_url", "t_news_events", ["url"], schema="market_data"
    )
    op.create_unique_constraint(
        "uq_t_company_events_asset_id",
        "t_company_events",
        ["asset_id", "ts", "event_type"],
        schema="market_data",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_t_company_events_asset_id", "t_company_events", schema="market_data", type_="unique"
    )
    op.drop_constraint(
        "uq_t_news_events_url", "t_news_events", schema="market_data", type_="unique"
    )
