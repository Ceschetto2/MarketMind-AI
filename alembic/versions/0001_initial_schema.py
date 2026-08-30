"""Schema iniziale: 3 schema Postgres, 10 tabelle, hypertable prezzi, audit trigger.

Rispecchia colonna per colonna l'`erDiagram` confermato in
`Market Mind AI - Docs/Architettura/01_schema_dati_er.md` (vault Obsidian
esterno al repo, percorso in CLAUDE.md), comprese le decisioni del 29-08-26:
PK di `t_market_prices` estesa a `(asset_id, ts, source)`; colonna `ts`
unica su `t_model_runs`/`t_model_decisions` (al posto di `run_ts`/
`decision_ts`, agenda #19); retention safeguard di un anno sui prezzi
(agenda #12); `t_audit_logs` popolata da un trigger Postgres generico
(agenda #17), agganciato alle cinque tabelle di ingestion di `market_data`
(non a `decisions`): è lì che serve risalire da una riga a chi l'ha scritta.

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

# (tabella qualificata, colonne PK) per il trigger generico di audit — solo
# le cinque tabelle di ingestion di market_data (01_schema_dati_er.md: "log
# ... pensato per risalire da una riga qualsiasi di market_data al run di
# ingestion che l'ha scritta o modificata").
AUDITED_TABLES: list[tuple[str, tuple[str, ...]]] = [
    ("market_data.t_assets", ("asset_id",)),
    ("market_data.t_market_prices", ("asset_id", "ts", "source")),
    ("market_data.t_news_events", ("news_event_id",)),
    ("market_data.t_macro_events", ("indicator", "ts")),
    ("market_data.t_company_events", ("company_event_id",)),
]


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    # --- market_data.t_assets ---
    op.create_table(
        "t_assets",
        sa.Column("asset_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("sector", sa.Text()),
        sa.Column("asset_type", sa.String(20), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("fetched_at", TIMESTAMP(timezone=True), nullable=False),
        sa.UniqueConstraint("symbol"),
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
        sa.Column("open", sa.Double(), nullable=False),
        sa.Column("high", sa.Double(), nullable=False),
        sa.Column("low", sa.Double(), nullable=False),
        sa.Column("close", sa.Double(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("fetched_at", TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("asset_id", "ts", "source", name="pk_t_market_prices"),
        schema="market_data",
    )
    op.execute(
        "SELECT create_hypertable('market_data.t_market_prices', 'ts', if_not_exists => TRUE)"
    )
    # Retention safeguard di un anno (agenda #12, da rivedere in base a test futuri).
    op.execute(
        "SELECT add_retention_policy('market_data.t_market_prices', INTERVAL '1 year')"
    )

    # --- market_data.t_news_events ---
    op.create_table(
        "t_news_events",
        sa.Column("news_event_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.BigInteger(),
            sa.ForeignKey("market_data.t_assets.asset_id", name="fk_t_news_events_asset_id_t_assets"),
        ),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("ts", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("raw_payload", JSONB(), nullable=False),
        sa.Column("sentiment_score", sa.Double()),
        sa.Column("fetched_at", TIMESTAMP(timezone=True), nullable=False),
        schema="market_data",
    )
    op.create_index(
        "ib_news_events_asset_ts",
        "t_news_events",
        ["asset_id", "ts"],
        schema="market_data",
    )
    op.create_index(
        "ib_news_events_unlinked",
        "t_news_events",
        ["ts"],
        schema="market_data",
        postgresql_where=sa.text("asset_id IS NULL"),
    )

    # --- market_data.t_macro_events --- (chiave naturale, nessun id surrogato)
    op.create_table(
        "t_macro_events",
        sa.Column("indicator", sa.Text(), primary_key=True),
        sa.Column("ts", sa.Date(), primary_key=True),
        sa.Column("value", sa.Double()),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("fetched_at", TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("indicator", "ts", name="pk_t_macro_events"),
        schema="market_data",
    )

    # --- market_data.t_company_events ---
    op.create_table(
        "t_company_events",
        sa.Column("company_event_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.BigInteger(),
            sa.ForeignKey("market_data.t_assets.asset_id", name="fk_t_company_events_asset_id_t_assets"),
            nullable=False,
        ),
        sa.Column("ts", sa.Date(), nullable=False),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("raw_payload", JSONB(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("fetched_at", TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('earnings', 'dividend', 'split')", name="event_type"
        ),
        schema="market_data",
    )
    op.create_index(
        "ib_company_events_asset_ts",
        "t_company_events",
        ["asset_id", "ts"],
        schema="market_data",
    )

    # --- decisions.t_model_runs ---
    op.create_table(
        "t_model_runs",
        sa.Column("run_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ts", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("config", JSONB(), nullable=False),
        sa.Column("llm_provider", sa.String(50), nullable=False),
        sa.Column("model_version", sa.String(100), nullable=False),
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
        sa.Column("confidence", sa.Double()),
        sa.Column("reasoning", sa.Text()),
        sa.Column("context_snapshot", JSONB(), nullable=False),
        sa.CheckConstraint("decision IN ('BUY', 'SELL', 'HOLD')", name="decision"),
        schema="decisions",
    )
    op.create_index(
        "ib_model_decisions_asset_ts",
        "t_model_decisions",
        ["asset_id", "ts"],
        schema="decisions",
    )
    op.create_index(
        "ib_model_decisions_run_id",
        "t_model_decisions",
        ["run_id"],
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
        sa.Column("pnl", sa.Double(), nullable=False),
        sa.Column("sharpe_ratio", sa.Double()),
        sa.Column("max_drawdown", sa.Double()),
        sa.Column("win_rate", sa.Double()),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        schema="decisions",
    )
    op.create_index(
        "ib_backtest_results_run_id",
        "t_backtest_results",
        ["run_id"],
        schema="decisions",
    )

    # --- audit.t_ingestion_runs ---
    op.create_table(
        "t_ingestion_runs",
        sa.Column("run_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("target_table", sa.String(100), nullable=False),
        sa.Column("started_at", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finished_at", TIMESTAMP(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("rows_written", sa.Integer()),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint(
            "source IN ('yfinance', 'gdelt', 'finnhub', 'fred', 'fmp')", name="source"
        ),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed', 'partial')", name="status"
        ),
        schema="audit",
    )
    op.create_index(
        "ib_ingestion_runs_source_started",
        "t_ingestion_runs",
        ["source", sa.text("started_at DESC")],
        schema="audit",
    )
    op.create_index(
        "ib_ingestion_runs_running",
        "t_ingestion_runs",
        ["status"],
        schema="audit",
        postgresql_where=sa.text("status = 'running'"),
    )

    # --- audit.t_audit_logs ---
    op.create_table(
        "t_audit_logs",
        sa.Column("audit_id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey("audit.t_ingestion_runs.run_id", name="fk_t_audit_logs_run_id_t_ingestion_runs"),
        ),
        sa.Column("schema_name", sa.String(50), nullable=False),
        sa.Column("table_name", sa.String(100), nullable=False),
        sa.Column("row_pk", sa.Text(), nullable=False),
        sa.Column("operation", sa.String(10), nullable=False),
        sa.Column("changed_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("diff", JSONB()),
        sa.CheckConstraint(
            "operation IN ('INSERT', 'UPDATE', 'DELETE')", name="operation"
        ),
        schema="audit",
    )
    op.create_index(
        "ib_audit_logs_table_row",
        "t_audit_logs",
        ["schema_name", "table_name", "row_pk"],
        schema="audit",
    )
    op.create_index(
        "ib_audit_logs_run_id", "t_audit_logs", ["run_id"], schema="audit"
    )
    op.create_index(
        "ir_audit_logs_changed_at",
        "t_audit_logs",
        ["changed_at"],
        schema="audit",
        postgresql_using="brin",
    )

    # --- trigger generico di audit ---
    # Parametrizzato via TG_ARGV con i nomi delle colonne PK della tabella
    # agganciata (una o più, per gestire sia PK singole sia composite), per
    # restare un'unica funzione condivisa da tutte le tabelle audited invece
    # di una per tabella. `run_id` è popolato leggendo una GUC di sessione
    # (`marketmind.ingestion_run_id`) che lo strato di scrittura in `db/`
    # può impostare con `SET LOCAL` prima di scrivere durante un run di
    # ingestion tracciato; resta NULL per modifiche non originate da un run
    # tracciato (coerente con la nullability di `t_audit_logs.run_id`).
    op.execute(
        """
        CREATE FUNCTION audit.fn_audit_log() RETURNS trigger AS $$
        DECLARE
            v_row jsonb;
            v_row_pk text := '';
            v_key text;
        BEGIN
            v_row := CASE WHEN TG_OP = 'DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;

            FOR i IN 0 .. TG_NARGS - 1 LOOP
                v_key := TG_ARGV[i];
                IF i > 0 THEN
                    v_row_pk := v_row_pk || ',';
                END IF;
                v_row_pk := v_row_pk || v_key || '=' || (v_row ->> v_key);
            END LOOP;

            INSERT INTO audit.t_audit_logs
                (run_id, schema_name, table_name, row_pk, operation, changed_at, diff)
            VALUES (
                NULLIF(current_setting('marketmind.ingestion_run_id', true), '')::bigint,
                TG_TABLE_SCHEMA,
                TG_TABLE_NAME,
                v_row_pk,
                TG_OP,
                now(),
                CASE TG_OP
                    WHEN 'INSERT' THEN jsonb_build_object('new', to_jsonb(NEW))
                    WHEN 'DELETE' THEN jsonb_build_object('old', to_jsonb(OLD))
                    ELSE jsonb_build_object('old', to_jsonb(OLD), 'new', to_jsonb(NEW))
                END
            );
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for qualified_table, pk_columns in AUDITED_TABLES:
        trigger_name = f"trg_audit_{qualified_table.split('.')[-1]}"
        args = ", ".join(f"'{col}'" for col in pk_columns)
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            AFTER INSERT OR UPDATE OR DELETE ON {qualified_table}
            FOR EACH ROW EXECUTE FUNCTION audit.fn_audit_log({args})
            """
        )


def downgrade() -> None:
    for qualified_table, _ in AUDITED_TABLES:
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
