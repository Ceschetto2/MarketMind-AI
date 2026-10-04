"""Payload grezzo in uno schema dedicato `raw`, fuori dalle tabelle raffinate.

Rispecchia colonna per colonna l'`erDiagram` confermato in
`Market Mind AI - Docs/db/01_schema_dati_er.md` (refactor del 05-09-26,
agenda #43). Ribalta una decisione precedente, verbalizzata in
`Market Mind AI.md` §7 e nella tabella decisioni §10 ("Pattern ingestion
dati: solo tabelle normalizzate, niente bronze/silver — provenienza inline";
"Medaglion Architecture: Scartata con riserva"): tenere `raw_payload` in
JSONB inline sulla tabella raffinata, invece che su una tabella bronze
separata, era stato scelto per non perdere flessibilità/informazione senza
introdurre un livello di staging. Il ripensamento non riabilita un vero
pattern bronze/silver multi-stadio (nessuna pipeline di riprocessamento
separata: raffinata e grezza restano scritte nella stessa transazione dallo
stesso script di ingestion) — riguarda solo *dove* il blob vive fisicamente,
non *quando* viene scritto.

Interessa `market_data.t_news_events` e `market_data.t_company_events`, le
uniche due tabelle che portavano `raw_payload` (`t_market_prices`,
`t_assets`, `t_macro_events` sono già completamente tipizzate, nessun
cambiamento). Per ciascuna, una nuova tabella 1:1 in `raw`
(`t_news_events_raw`, `t_company_events_raw`): la chiave primaria è la
stessa chiave surrogata della tabella raffinata, anche FK verso di essa con
`ON DELETE CASCADE`, più `source`/`fetched_at` duplicate (autosufficienza
per query di retention/purge per età senza join) e `raw_payload` stesso.
Motivazione completa (permessi/retention differenziati per i blob grezzi,
stesso criterio già applicato a `audit`/`portfolio`) in
`db/models/raw.py` e `db/01_schema_dati_er.md`.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-05

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS raw")

    # --- raw.t_news_events_raw ---
    op.create_table(
        "t_news_events_raw",
        sa.Column(
            "news_event_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "market_data.t_news_events.news_event_id",
                name="fk_t_news_events_raw_news_event_id_t_news_events",
                ondelete="CASCADE",
            ),
            primary_key=True,
        ),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("fetched_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("raw_payload", JSONB(), nullable=False),
        schema="raw",
    )
    op.create_index(
        "ir_news_events_raw_fetched_at",
        "t_news_events_raw",
        ["fetched_at"],
        schema="raw",
        postgresql_using="brin",
    )

    # --- raw.t_company_events_raw ---
    op.create_table(
        "t_company_events_raw",
        sa.Column(
            "company_event_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "market_data.t_company_events.company_event_id",
                name="fk_t_company_events_raw_company_event_id_t_company_events",
                ondelete="CASCADE",
            ),
            primary_key=True,
        ),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("fetched_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("raw_payload", JSONB(), nullable=False),
        schema="raw",
    )
    op.create_index(
        "ir_company_events_raw_fetched_at",
        "t_company_events_raw",
        ["fetched_at"],
        schema="raw",
        postgresql_using="brin",
    )

    # Nessuna pipeline di ingestion scrive ancora in produzione, ma le
    # istanze reali potrebbero già avere righe di test: migrarle prima di
    # rimuovere la colonna, invece di assumere le tabelle vuote.
    op.execute(
        """
        INSERT INTO raw.t_news_events_raw (news_event_id, source, fetched_at, raw_payload)
        SELECT news_event_id, source, fetched_at, raw_payload
        FROM market_data.t_news_events
        """
    )
    op.execute(
        """
        INSERT INTO raw.t_company_events_raw (company_event_id, source, fetched_at, raw_payload)
        SELECT company_event_id, source, fetched_at, raw_payload
        FROM market_data.t_company_events
        """
    )

    op.drop_column("t_news_events", "raw_payload", schema="market_data")
    op.drop_column("t_company_events", "raw_payload", schema="market_data")


def downgrade() -> None:
    op.add_column(
        "t_news_events",
        sa.Column("raw_payload", JSONB(), nullable=True),
        schema="market_data",
    )
    op.add_column(
        "t_company_events",
        sa.Column("raw_payload", JSONB(), nullable=True),
        schema="market_data",
    )

    op.execute(
        """
        UPDATE market_data.t_news_events AS n
        SET raw_payload = r.raw_payload
        FROM raw.t_news_events_raw AS r
        WHERE r.news_event_id = n.news_event_id
        """
    )
    op.execute(
        """
        UPDATE market_data.t_company_events AS c
        SET raw_payload = r.raw_payload
        FROM raw.t_company_events_raw AS r
        WHERE r.company_event_id = c.company_event_id
        """
    )

    op.alter_column(
        "t_news_events", "raw_payload", nullable=False, schema="market_data"
    )
    op.alter_column(
        "t_company_events", "raw_payload", nullable=False, schema="market_data"
    )

    op.drop_table("t_news_events_raw", schema="raw")
    op.drop_table("t_company_events_raw", schema="raw")

    op.execute("DROP SCHEMA IF EXISTS raw CASCADE")
