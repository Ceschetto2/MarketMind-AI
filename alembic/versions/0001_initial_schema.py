"""Schema iniziale: 3 schema Postgres, 10 tabelle, hypertable prezzi, audit trigger.

Rispecchia le decisioni in CLAUDE.md (revisione del 29-08-26): PK di
`t_market_prices` estesa a `(asset_id, ts, source)`, colonna `ts` unica su
`t_model_runs`/`t_model_decisions` (al posto di `run_ts`/`decision_ts`),
retention safeguard di un anno sui prezzi, `t_audit_logs` popolata da
trigger Postgres.

I dettagli di colonna non esplicitamente decisi in CLAUDE.md (es. i campi
di `t_news_events`/`t_macro_events`/`t_company_events`) sono una prima
bozza ragionevole in assenza del documento ER
(`Market Mind AI - Docs/Architettura/01_schema_dati_er.md`, non presente in
questo checkout) — da rivedere non appena disponibile.

Revision ID: 0001
Revises:
Create Date: 2026-08-29

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMAS = ("market_data", "decisions", "audit")

# Tabelle su cui è agganciato il trigger generico di audit: quelle a basso
# volume/rilevanza operativa. Le tabelle di ingestion ad alto volume
# (t_market_prices, t_news_events, t_macro_events, t_company_events) sono
# escluse: la provenienza è già in source/fetched_at/raw_payload e un audit
# riga-per-riga ne moltiplicherebbe inutilmente il volume dati.
AUDITED_TABLES = (
    "market_data.t_assets",
    "decisions.t_model_runs",
    "decisions.t_model_decisions",
    "decisions.t_backtest_results",
    "audit.t_ingestion_runs",
)


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    # --- market_data.t_assets ---
    op.create_table(
        "t_assets",
        sa.Column("asset_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("name", sa.Text()),
        sa.Column("exchange", sa.String(20)),
        sa.Column("sector", sa.Text()),
        sa.Column("industry", sa.Text()),
        sa.Column("currency", sa.String(10)),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("updated_at", TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("symbol", name="uq_t_assets_symbol"),
        schema="market_data",
    )

    # --- market_data.t_market_prices (hypertable) ---
    op.create_table(
        "t_market_prices",
        sa.Column(
            "asset_id",
            sa.BigInteger(),
            sa.ForeignKey("market_data.t_assets.asset_id", name="fk_t_market_prices_asset_id_t_assets"),
            nullable=False,
        ),
        sa.Column("ts", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("open", sa.Numeric(18, 6)),
        sa.Column("high", sa.Numeric(18, 6)),
        sa.Column("low", sa.Numeric(18, 6)),
        sa.Column("close", sa.Numeric(18, 6)),
        sa.Column("volume", sa.BigInteger()),
        sa.Column("fetched_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("raw_payload", JSONB()),
        sa.PrimaryKeyConstraint("asset_id", "ts", "source", name="pk_t_market_prices"),
        schema="market_data",
    )
    op.execute(
        "SELECT create_hypertable('market_data.t_market_prices', 'ts', if_not_exists => TRUE)"
    )
    # Retention safeguard di un anno (da rivedere in base a test futuri, CLAUDE.md).
    op.execute(
        "SELECT add_retention_policy('market_data.t_market_prices', INTERVAL '1 year')"
    )

    # --- market_data.t_news_events ---
    op.create_table(
        "t_news_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.BigInteger(),
            sa.ForeignKey("market_data.t_assets.asset_id", name="fk_t_news_events_asset_id_t_assets"),
        ),
        sa.Column("event_ts", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("title", sa.Text()),
        sa.Column("url", sa.Text()),
        sa.Column("tone", sa.Numeric(9, 4)),
        sa.Column("fetched_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("raw_payload", JSONB()),
        schema="market_data",
    )
    op.create_index(
        "ix_t_news_events_asset_id_event_ts",
        "t_news_events",
        ["asset_id", "event_ts"],
        schema="market_data",
    )

    # --- market_data.t_macro_events ---
    op.create_table(
        "t_macro_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("indicator_code", sa.String(50), nullable=False),
        sa.Column("event_ts", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("vintage_date", TIMESTAMP(timezone=True)),
        sa.Column("value", sa.Numeric(20, 8)),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("fetched_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("raw_payload", JSONB()),
        schema="market_data",
    )
    op.create_index(
        "ix_t_macro_events_indicator_code_event_ts",
        "t_macro_events",
        ["indicator_code", "event_ts"],
        schema="market_data",
    )

    # --- market_data.t_company_events ---
    op.create_table(
        "t_company_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.BigInteger(),
            sa.ForeignKey("market_data.t_assets.asset_id", name="fk_t_company_events_asset_id_t_assets"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("event_ts", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("fetched_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("raw_payload", JSONB()),
        schema="market_data",
    )
    op.create_index(
        "ix_t_company_events_asset_id_event_ts",
        "t_company_events",
        ["asset_id", "event_ts"],
        schema="market_data",
    )

    # --- decisions.t_model_runs ---
    op.create_table(
        "t_model_runs",
        sa.Column("run_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ts", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("llm_provider", sa.String(50), nullable=False),
        sa.Column("llm_model", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("run_metadata", JSONB()),
        sa.Column(
            "created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="status",
        ),
        schema="decisions",
    )

    # --- decisions.t_model_decisions ---
    op.create_table(
        "t_model_decisions",
        sa.Column("decision_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey("decisions.t_model_runs.run_id", name="fk_t_model_decisions_run_id_t_model_runs"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            sa.BigInteger(),
            sa.ForeignKey("market_data.t_assets.asset_id", name="fk_t_model_decisions_asset_id_t_assets"),
            nullable=False,
        ),
        sa.Column("ts", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("rationale", sa.Text()),
        sa.Column("context_window", JSONB()),
        sa.Column(
            "created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.UniqueConstraint("run_id", "asset_id", name="uq_t_model_decisions_run_id_asset_id"),
        sa.CheckConstraint("decision IN ('BUY', 'SELL', 'HOLD')", name="decision"),
        schema="decisions",
    )

    # --- decisions.t_backtest_results ---
    op.create_table(
        "t_backtest_results",
        sa.Column("backtest_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey("decisions.t_model_runs.run_id", name="fk_t_backtest_results_run_id_t_model_runs"),
            nullable=False,
        ),
        sa.Column("period_start", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("period_end", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("metrics", JSONB(), nullable=False),
        sa.Column(
            "created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        schema="decisions",
    )

    # --- audit.t_ingestion_runs ---
    op.create_table(
        "t_ingestion_runs",
        sa.Column("ingestion_run_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("target_table", sa.String(100), nullable=False),
        sa.Column("started_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finished_at", TIMESTAMP(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("records_ingested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')", name="status"
        ),
        schema="audit",
    )

    # --- audit.t_audit_logs ---
    op.create_table(
        "t_audit_logs",
        sa.Column("audit_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("table_schema", sa.String(50), nullable=False),
        sa.Column("table_name", sa.String(100), nullable=False),
        sa.Column("operation", sa.String(10), nullable=False),
        sa.Column("old_data", JSONB()),
        sa.Column("new_data", JSONB()),
        sa.Column(
            "changed_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.CheckConstraint(
            "operation IN ('INSERT', 'UPDATE', 'DELETE')", name="operation"
        ),
        schema="audit",
    )

    # --- trigger generico di audit (audit.t_audit_logs popolata da trigger, non da app) ---
    op.execute(
        """
        CREATE FUNCTION audit.fn_audit_log() RETURNS trigger AS $$
        BEGIN
            INSERT INTO audit.t_audit_logs (table_schema, table_name, operation, old_data, new_data)
            VALUES (
                TG_TABLE_SCHEMA,
                TG_TABLE_NAME,
                TG_OP,
                CASE WHEN TG_OP IN ('UPDATE', 'DELETE') THEN to_jsonb(OLD) ELSE NULL END,
                CASE WHEN TG_OP IN ('UPDATE', 'INSERT') THEN to_jsonb(NEW) ELSE NULL END
            );
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for qualified_table in AUDITED_TABLES:
        trigger_name = f"trg_audit_{qualified_table.split('.')[-1]}"
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            AFTER INSERT OR UPDATE OR DELETE ON {qualified_table}
            FOR EACH ROW EXECUTE FUNCTION audit.fn_audit_log()
            """
        )


def downgrade() -> None:
    for qualified_table in AUDITED_TABLES:
        trigger_name = f"trg_audit_{qualified_table.split('.')[-1]}"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {qualified_table}")
    op.execute("DROP FUNCTION IF EXISTS audit.fn_audit_log()")

    op.drop_table("t_audit_logs", schema="audit")
    op.drop_table("t_ingestion_runs", schema="audit")
    op.drop_table("t_backtest_results", schema="decisions")
    op.drop_table("t_model_decisions", schema="decisions")
    op.drop_table("t_model_runs", schema="decisions")
    op.drop_table("t_company_events", schema="market_data")
    op.drop_table("t_macro_events", schema="market_data")
    op.drop_table("t_news_events", schema="market_data")
    op.drop_table("t_market_prices", schema="market_data")
    op.drop_table("t_assets", schema="market_data")

    for schema in SCHEMAS:
        op.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
