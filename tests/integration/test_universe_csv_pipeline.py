"""Test di integrazione per `run()` di `universe_csv_pipeline.py`.

Scrive per davvero su Postgres (stesso limite delle altre pipeline: `run()`
apre le proprie sessioni via `get_session()`, non quella della fixture
`db_session`) — CSV di prova su `tmp_path`, pulizia esplicita a fine test.
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from marketmind_ai.db.models.audit import IngestionRun
from marketmind_ai.db.models.market_data import Asset, UniverseMember
from marketmind_ai.db.session import get_session
from marketmind_ai.ingestion.universe_csv_pipeline import run

pytestmark = pytest.mark.integration

TEST_SYMBOLS = ["TESTX", "TESTSPY"]


class TestRunEndToEnd:
    def _cleanup(self) -> None:
        with get_session() as session:
            asset_ids = session.execute(
                select(Asset.asset_id).where(Asset.symbol.in_(TEST_SYMBOLS))
            ).scalars().all()
            if asset_ids:
                session.execute(
                    delete(UniverseMember).where(UniverseMember.asset_id.in_(asset_ids))
                )
                session.execute(delete(Asset).where(Asset.asset_id.in_(asset_ids)))
            session.execute(
                delete(IngestionRun).where(
                    IngestionRun.target_table == "market_data.t_universe_members"
                )
            )

    def test_run_crea_asset_e_membership(self, tmp_path):
        csv_path = tmp_path / "universe.csv"
        csv_path.write_text(
            "symbol,name,sector,asset_type,is_benchmark\n"
            "TESTX,Test Company,Technology,equity,false\n"
            "TESTSPY,Test SPY,,etf,true\n"
        )

        try:
            run(csv_path=csv_path)

            with get_session() as session:
                assets = {
                    a.symbol: a
                    for a in session.execute(
                        select(Asset).where(Asset.symbol.in_(TEST_SYMBOLS))
                    ).scalars()
                }
                assert set(assets) == set(TEST_SYMBOLS)
                assert assets["TESTX"].asset_type == "equity"
                assert assets["TESTSPY"].asset_type == "etf"

                members = {
                    m.asset_id: m
                    for m in session.execute(
                        select(UniverseMember).where(
                            UniverseMember.asset_id.in_(
                                a.asset_id for a in assets.values()
                            )
                        )
                    ).scalars()
                }
                assert members[assets["TESTX"].asset_id].is_benchmark is False
                assert members[assets["TESTSPY"].asset_id].is_benchmark is True

                audit_row = session.execute(
                    select(IngestionRun)
                    .where(
                        IngestionRun.target_table == "market_data.t_universe_members"
                    )
                    .order_by(IngestionRun.run_id.desc())
                ).scalars().first()
                assert audit_row.status == "success"
                assert audit_row.rows_written == 2
        finally:
            self._cleanup()

    def test_run_e_idempotente(self, tmp_path):
        csv_path = tmp_path / "universe.csv"
        csv_path.write_text(
            "symbol,name,sector,asset_type,is_benchmark\n"
            "TESTX,Test Company,Technology,equity,false\n"
        )

        try:
            run(csv_path=csv_path)
            run(csv_path=csv_path)

            with get_session() as session:
                rows = session.execute(
                    select(Asset).where(Asset.symbol == "TESTX")
                ).scalars().all()
                assert len(rows) == 1
        finally:
            self._cleanup()
