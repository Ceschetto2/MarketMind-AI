"""Disambigua le due pipeline GDELT nel CHECK di `audit.t_ingestion_runs.source`.

Drift preesistente segnalato ma non chiuso da `0004` (vedi il suo docstring,
§ "Effetto collaterale scoperto"): `Market Mind AI - Docs/db/01_schema_dati_er.md`
e `Market Mind AI - Docs/pipelines/01_trigger_e_scheduling.md` documentano che
la pipeline Web NGrams scrive `source='gdelt-ngrams'` (non il generico
`gdelt`) per restare distinguibile da una futura pipeline DOC API
(`gdelt-doc`) — le due scriverebbero altrimenti la stessa coppia
`(source, target_table)` in `t_ingestion_runs`, indistinguibili. Il CHECK
`ck_t_ingestion_runs_source` in vigore (`0001`, allargato da `0004` per
`universe-csv`) ammette ancora solo il generico `gdelt`.

Il generico `gdelt` resta comunque ammesso, non rimosso: nessuna pipeline
esistente lo scrive ancora (nessuna pipeline di ingestion è stata scritta
finora), quindi rimuoverlo non romperebbe nulla di reale — ma toglierlo
richiederebbe comunque un valore di transizione se in futuro servisse una
fonte GDELT ancora indifferenziata, e non c'è motivo di stringere il
vincolo più del necessario in questa migrazione.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-06

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "source",
        "t_ingestion_runs",
        schema="audit",
        type_="check",
    )
    op.create_check_constraint(
        "source",
        "t_ingestion_runs",
        "source IN ('yfinance', 'gdelt', 'gdelt-ngrams', 'gdelt-doc', 'finnhub', 'fred', 'fmp', 'universe-csv')",
        schema="audit",
    )


def downgrade() -> None:
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
