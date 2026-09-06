"""Universe membership: `t_universe_members` in `market_data`, chiude agenda #31.

Rispecchia colonna per colonna l'`erDiagram` confermato in
`Market Mind AI - Docs/db/01_schema_dati_er.md` (deciso il 30-08-26).
Problema risolto: SPY deve entrare in `t_assets` per riusare la pipeline di
ingestion esistente (prezzi/news/earnings), ma non deve mai comparire nella
lista di ticker su cui gira il motore decisionale. Soluzione: non una
colonna su `t_assets`, ma una tabella separata con FK 1:1 opzionale
verso `t_assets` (`asset_id` come PK e FK, nessuna duplicazione di
`symbol`/`name`) più `is_benchmark` booleano (`true` solo per SPY) e le
consuete colonne di provenienza `source`/`fetched_at`. Il motore
decisionale, quando esisterà, leggerà `WHERE is_benchmark = false`; le
pipeline di ingestion leggono tutta la tabella, perché SPY ha comunque
bisogno dei propri dati di prezzo per l'equity curve del benchmark.

Sesta tabella di ingestion di `market_data`: rientra quindi nell'ambito
di `AUDITED_TABLES` (0001) — è mutabile (upsert quando il CSV di seed
viene aggiornato), non insert-only come le tabelle `raw` escluse in 0003.
Aggiunto anche `trg_audit_t_universe_members`, che riusa la funzione
generica `audit.fn_audit_log()` già esistente.

Effetto collaterale scoperto implementando questa migrazione: la pipeline
`universe-csv` che alimenterà questa tabella deve poter loggare le proprie
esecuzioni in `audit.t_ingestion_runs`, ma il CHECK `ck_t_ingestion_runs_source`
(0001) ammette solo `('yfinance', 'gdelt', 'finnhub', 'fred', 'fmp')` —
`universe-csv` non era previsto. Il vincolo viene allargato qui per non
lasciare un blocco silente alla prima esecuzione reale della pipeline.
Nota: `01_schema_dati_er.md` (§ `t_ingestion_runs`) elenca anche
`gdelt-ngrams`/`gdelt-doc` al posto del generico `gdelt` già in vigore —
drift preesistente, non toccato qui perché indipendente da questa
migrazione.

Non ancora implementati: lo script della pipeline `universe-csv` e il CSV
di seed stesso (`seeds/universe.csv`), l'interfaccia Pydantic
`UniverseMemberRecord` in `schemas/` (documentata in
`Data Providers/00_schema_interfacce.md`, modulo `schemas/` non ancora
iniziato per nessuna delle sei interfacce).

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-06

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "t_universe_members",
        sa.Column(
            "asset_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "market_data.t_assets.asset_id",
                name="fk_t_universe_members_asset_id_t_assets",
            ),
            primary_key=True,
        ),
        sa.Column("is_benchmark", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("fetched_at", TIMESTAMP(timezone=True), nullable=False),
        schema="market_data",
    )

    # `universe-csv` come nuova fonte valida per `t_ingestion_runs.source`.
    # Nome breve ("source"): la naming convention di `Base.metadata` lo
    # espande da sola in `ck_t_ingestion_runs_source` — passare già il nome
    # completo lo farebbe espandere una seconda volta (visto in preview).
    op.drop_constraint(
        "source",
        "t_ingestion_runs",
        schema="audit",
        type_="check",
    )
    op.create_check_constraint(
        "source",
        "t_ingestion_runs",
        "source IN ('yfinance', 'gdelt', 'finnhub', 'fred', 'fmp', 'universe-csv')",
        schema="audit",
    )

    op.execute(
        """
        CREATE TRIGGER trg_audit_t_universe_members
        AFTER INSERT OR UPDATE OR DELETE ON market_data.t_universe_members
        FOR EACH ROW EXECUTE FUNCTION audit.fn_audit_log('asset_id')
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_audit_t_universe_members ON market_data.t_universe_members"
    )

    op.drop_constraint(
        "source",
        "t_ingestion_runs",
        schema="audit",
        type_="check",
    )
    op.create_check_constraint(
        "source",
        "t_ingestion_runs",
        "source IN ('yfinance', 'gdelt', 'finnhub', 'fred', 'fmp')",
        schema="audit",
    )

    op.drop_table("t_universe_members", schema="market_data")
