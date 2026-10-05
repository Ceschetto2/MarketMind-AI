"""Test di integrazione dello strato generico di `marketmind_db`
(`Database`, `TableRepository`, `IngestionRunAudit`) contro Postgres reale.

Tutto gira dentro una transazione esterna sempre annullata a fine test
(fixture `rollback_db` in `conftest.py`): anche i
`commit()` di `Database.session()` diventano `SAVEPOINT`, nessun dato di
prova resta nel DB — nemmeno l'asset sintetico `TESTREPO`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from marketmind_db.access import INGESTION
from marketmind_db.audit import IngestionRunAudit
from marketmind_db.database import Database, DatabaseSettings
from marketmind_db.models.audit import AuditLog, IngestionRun
from marketmind_db.models.market_data import Asset, CompanyEvent, MacroEvent, MarketPrice, NewsEvent
from marketmind_db.models.raw import NewsEventRaw

pytestmark = pytest.mark.integration

_SYMBOL = "TESTREPO"
_NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
def db(rollback_db: Database) -> Database:
    return rollback_db


@pytest.fixture
def asset_id(db: Database) -> int:
    with db.transaction() as tx:
        [row] = tx.repository(Asset).upsert_returning(
            [
                {
                    "symbol": _SYMBOL,
                    "name": "Test Repository Inc.",
                    "asset_type": "equity",
                    "source": "test",
                    "fetched_at": _NOW,
                }
            ],
            conflict_on=("symbol",),
            returning=("asset_id",),
        )
    return row["asset_id"]


def _price(asset_id: int, hours_ago: int, close: float) -> dict:
    return {
        "asset_id": asset_id,
        "ts": _NOW - timedelta(hours=hours_ago),
        "source": "test",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1,
        "fetched_at": _NOW,
    }


class TestUpsert:
    def test_insert_poi_upsert_aggiorna_senza_duplicare(self, db, asset_id):
        key = ("asset_id", "ts", "source")
        with db.transaction() as tx:
            prices = tx.repository(MarketPrice)
            assert prices.upsert([_price(asset_id, h, 10.0) for h in range(3)], conflict_on=key) == 3
            prices.upsert([_price(asset_id, 0, 99.0)], conflict_on=key)

            rows = prices.select(where={"asset_id": asset_id}, order_by=("-ts",))

        assert len(rows) == 3
        assert rows[0].close == 99.0

    def test_do_nothing_non_sovrascrive(self, db, asset_id):
        key = ("asset_id", "ts", "source")
        with db.transaction() as tx:
            prices = tx.repository(MarketPrice)
            prices.upsert([_price(asset_id, 0, 10.0)], conflict_on=key)
            written = prices.upsert([_price(asset_id, 0, 99.0)], conflict_on=key, update=())
            [row] = prices.select(where={"asset_id": asset_id})

        assert written == 0
        assert row.close == 10.0

    def test_duplicati_nello_stesso_batch_non_fanno_fallire_lo_statement(self, db, asset_id):
        """Senza dedup Postgres rifiuta lo statement ("ON CONFLICT DO UPDATE
        command cannot affect row a second time")."""
        with db.transaction() as tx:
            prices = tx.repository(MarketPrice)
            prices.upsert(
                [_price(asset_id, 0, 1.0), _price(asset_id, 0, 2.0)],
                conflict_on=("asset_id", "ts", "source"),
            )
            [row] = prices.select(where={"asset_id": asset_id})

        assert row.close == 2.0

    def test_upsert_returning_e_payload_raw_nella_stessa_transazione(self, db, asset_id):
        news = {
            "asset_id": asset_id,
            "source": "test",
            "ts": _NOW,
            "headline": "titolo",
            "url": "https://example.test/testrepo/1",
            "fetched_at": _NOW,
        }
        with db.transaction() as tx:
            [first] = tx.repository(NewsEvent).upsert_returning(
                [news], conflict_on=("url",), returning=("news_event_id", "url")
            )
            [again] = tx.repository(NewsEvent).upsert_returning(
                [news | {"headline": "aggiornato"}],
                conflict_on=("url",),
                returning=("news_event_id", "url"),
            )
            tx.repository(NewsEventRaw).upsert(
                [
                    {
                        "news_event_id": again["news_event_id"],
                        "source": "test",
                        "fetched_at": _NOW,
                        "raw_payload": {"k": "v"},
                    }
                ],
                conflict_on=("news_event_id",),
            )
            stored = tx.repository(NewsEvent).get_one(url=news["url"])
            raw = tx.repository(NewsEventRaw).get_one(news_event_id=again["news_event_id"])

        assert first["news_event_id"] == again["news_event_id"]
        assert stored.headline == "aggiornato"
        assert raw.raw_payload == {"k": "v"}

    def test_chiave_composita_unique_constraint(self, db, asset_id):
        event = {
            "asset_id": asset_id,
            "ts": date.today(),
            "event_type": "earnings",
            "source": "test",
            "fetched_at": _NOW,
        }
        key = ("asset_id", "ts", "event_type", "source")
        with db.transaction() as tx:
            events = tx.repository(CompanyEvent)
            events.upsert([event], conflict_on=key)
            events.upsert([event | {"source": "test-2"}], conflict_on=key)
            events.upsert([event | {"fiscal_year": "2026"}], conflict_on=key)
            rows = events.select(where={"asset_id": asset_id}, order_by=("source",))

        assert [(r.source, r.fiscal_year) for r in rows] == [("test", "2026"), ("test-2", None)]


class TestLettureEScritture:
    def test_values_count_update_delete(self, db, asset_id):
        key = ("asset_id", "ts", "source")
        with db.transaction() as tx:
            prices = tx.repository(MarketPrice)
            prices.upsert([_price(asset_id, h, float(h)) for h in range(4)], conflict_on=key)

            assert prices.count(where={"asset_id": asset_id}) == 4
            assert sorted(prices.values("close", where={"asset_id": asset_id})) == [0.0, 1.0, 2.0, 3.0]
            assert prices.select(where={"asset_id": asset_id}, limit=2).__len__() == 2

            updated = prices.update(
                {"volume": 7}, where=[MarketPrice.asset_id == asset_id, MarketPrice.close >= 2.0]
            )
            deleted = prices.delete(where={"asset_id": asset_id, "close": [0.0, 1.0]})

            remaining = prices.select(where={"asset_id": asset_id}, order_by=("close",))

        assert updated == 2
        assert deleted == 2
        assert [(r.close, r.volume) for r in remaining] == [(2.0, 7), (3.0, 7)]

    def test_get_one_restituisce_none_se_assente(self, db):
        with db.transaction() as tx:
            assert tx.repository(Asset).get_one(symbol="NON-ESISTE-XYZ") is None


class TestLatestPer:
    def test_ultima_riga_per_indicatore_fino_a_una_data(self, db):
        rows = [
            {"indicator": ind, "ts": ts, "value": v, "source": "test", "fetched_at": _NOW}
            for ind, ts, v in [
                ("TESTREPO_A", date(2026, 7, 1), 1.0),
                ("TESTREPO_A", date(2026, 8, 1), 2.0),
                ("TESTREPO_A", date(2026, 9, 1), 3.0),
                ("TESTREPO_B", date(2026, 6, 1), 10.0),
            ]
        ]
        with db.transaction() as tx:
            macro = tx.repository(MacroEvent)
            macro.insert(rows)
            latest = macro.latest_per(
                ("indicator",),
                order_by="ts",
                where=[MacroEvent.indicator.in_(["TESTREPO_A", "TESTREPO_B"]), MacroEvent.ts <= date(2026, 8, 15)],
            )

        assert sorted((r.indicator, r.value) for r in latest) == [("TESTREPO_A", 2.0), ("TESTREPO_B", 10.0)]


class TestDatabase:
    def test_statement_timeout_interrompe_query_lente(self, db):
        slow = Database(DatabaseSettings(url="unused", statement_timeout_ms=50), policy=INGESTION, bind=db.bind)

        with pytest.raises(OperationalError, match="statement timeout"):
            with slow.session() as session:
                session.execute(text("SELECT pg_sleep(0.5)"))

    def test_dry_run_audit_e_dati_coerenti_poi_annullati(self, db, asset_id):
        """Il caso trovato col primo `--dry-run` reale: la riga di audit
        annullata subito faceva violare a ogni scrittura successiva la FK di
        `t_audit_logs.run_id`."""
        with Database(DatabaseSettings(url="unused"), policy=INGESTION, bind=db.bind, dry_run=True) as dry:
            with IngestionRunAudit(dry, "fred", "market_data.t_macro_events") as run:
                with dry.transaction() as tx:
                    tx.repository(MacroEvent).insert(
                        [{"indicator": "TESTREPO_DRY", "ts": date.today(), "value": 1.0,
                          "source": "test", "fetched_at": _NOW}]
                    )

        with db.session() as session:
            assert session.get(IngestionRun, run.run_id) is None
        with db.transaction() as tx:
            assert tx.repository(MacroEvent).count(where={"indicator": "TESTREPO_DRY"}) == 0

    def test_dry_run_non_lascia_scritture(self, db, asset_id):
        with Database(DatabaseSettings(url="unused"), policy=INGESTION, bind=db.bind, dry_run=True) as dry:
            with dry.transaction() as tx:
                tx.repository(MarketPrice).insert([_price(asset_id, 0, 1.0)])
            with dry.transaction() as tx:
                # dentro il dry run la scrittura è visibile alle sessioni successive
                assert tx.repository(MarketPrice).count(where={"asset_id": asset_id}) == 1

        with db.transaction() as tx:
            assert tx.repository(MarketPrice).count(where={"asset_id": asset_id}) == 0


class TestIngestionRunAudit:
    def test_successo_e_collegamento_audit_log(self, db):
        """Il collegamento si verifica su `t_macro_events`, non su
        `t_market_prices`: su una hypertable il trigger di audit registra il
        nome del chunk (`_timescaledb_internal._hyper_*_chunk`), non della
        tabella."""
        with IngestionRunAudit(db, "fred", "market_data.t_macro_events") as run:
            with db.transaction() as tx:
                tx.repository(MacroEvent).insert(
                    [
                        {
                            "indicator": "TESTREPO_IND",
                            "ts": date.today(),
                            "value": 1.0,
                            "source": "test",
                            "fetched_at": _NOW,
                        }
                    ]
                )
            run.rows_written += 1

        with db.session() as session:
            stored = session.get(IngestionRun, run.run_id)
            linked = session.execute(
                select(AuditLog.run_id).where(
                    AuditLog.table_name == "t_macro_events", AuditLog.run_id == run.run_id
                )
            ).scalars().all()

        assert stored.status == "success"
        assert stored.rows_written == 1
        assert stored.finished_at is not None
        assert linked == [run.run_id]

    def test_fallimento_registrato_anche_se_i_dati_vanno_in_rollback(self, db, asset_id):
        with pytest.raises(RuntimeError):
            with IngestionRunAudit(db, "yfinance", "market_data.t_market_prices") as run:
                with db.transaction() as tx:
                    tx.repository(MarketPrice).insert([_price(asset_id, 0, 1.0)])
                    raise RuntimeError("fetch esploso")

        with db.session() as session:
            stored = session.get(IngestionRun, run.run_id)
            prices = session.execute(
                select(MarketPrice).where(MarketPrice.asset_id == asset_id)
            ).scalars().all()

        assert stored.status == "failed"
        assert stored.error_message == "fetch esploso"
        assert prices == []

    def test_partial(self, db):
        with IngestionRunAudit(db, "fred", "market_data.t_macro_events") as run:
            run.mark_partial("1 su 4 fallito")

        with db.session() as session:
            assert session.get(IngestionRun, run.run_id).status == "partial"
