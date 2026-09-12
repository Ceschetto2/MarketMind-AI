"""Test di integrazione per `db/context_reader.py` — richiedono Postgres reale.

Tutte le funzioni accettano una sessione iniettata: usano la fixture
`db_session` (rollback automatico a fine test, anche sui `flush()`/scritture
di setup).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from marketmind_ai.db.context_reader import (
    get_decision_universe,
    get_latest_macro_events,
    get_recent_company_events,
    get_recent_news,
    get_recent_prices,
)
from marketmind_ai.db.models.market_data import (
    Asset,
    CompanyEvent,
    MacroEvent,
    MarketPrice,
    NewsEvent,
    UniverseMember,
)

pytestmark = pytest.mark.integration

TEST_ASSET_ID = -1
TEST_ASSET_ID_2 = -2
AS_OF = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def _make_asset(session, asset_id: int = TEST_ASSET_ID, symbol: str = "TESTX") -> int:
    asset = Asset(
        asset_id=asset_id,
        symbol=symbol,
        name="Test Asset",
        sector="Test",
        asset_type="equity",
        source="yfinance",
        fetched_at=datetime.now(timezone.utc),
    )
    session.add(asset)
    session.flush()
    return asset.asset_id


class TestGetRecentPrices:
    def test_filters_by_window_and_asset(self, db_session):
        asset_id = _make_asset(db_session)
        in_window = MarketPrice(
            asset_id=asset_id,
            ts=AS_OF - timedelta(days=1),
            source="yfinance",
            open=1,
            high=1,
            low=1,
            close=100.0,
            volume=10,
            fetched_at=datetime.now(timezone.utc),
        )
        out_of_window = MarketPrice(
            asset_id=asset_id,
            ts=AS_OF - timedelta(days=40),
            source="yfinance",
            open=1,
            high=1,
            low=1,
            close=200.0,
            volume=10,
            fetched_at=datetime.now(timezone.utc),
        )
        after_as_of = MarketPrice(
            asset_id=asset_id,
            ts=AS_OF + timedelta(hours=1),
            source="yfinance",
            open=1,
            high=1,
            low=1,
            close=300.0,
            volume=10,
            fetched_at=datetime.now(timezone.utc),
        )
        db_session.add_all([in_window, out_of_window, after_as_of])
        db_session.flush()

        prices = get_recent_prices(db_session, asset_id, as_of=AS_OF, days_back=30)

        assert [p.close for p in prices] == [100.0]

    def test_no_prices_returns_empty_list(self, db_session):
        asset_id = _make_asset(db_session)

        assert get_recent_prices(db_session, asset_id, as_of=AS_OF, days_back=30) == []


class TestGetRecentNews:
    def test_filters_by_window_asset_and_caps_max_items(self, db_session):
        asset_id = _make_asset(db_session)
        for i in range(3):
            db_session.add(
                NewsEvent(
                    asset_id=asset_id,
                    source="Finnhub",
                    ts=AS_OF - timedelta(days=i + 1),
                    headline=f"news {i}",
                    url=f"https://example.com/{i}",
                    fetched_at=datetime.now(timezone.utc),
                )
            )
        db_session.add(
            NewsEvent(
                asset_id=asset_id,
                source="Finnhub",
                ts=AS_OF - timedelta(days=10),
                headline="fuori finestra",
                url="https://example.com/old",
                fetched_at=datetime.now(timezone.utc),
            )
        )
        db_session.flush()

        news = get_recent_news(db_session, asset_id, as_of=AS_OF, days_back=7, max_items=2)

        assert len(news) == 2
        assert all("fuori finestra" != n.headline for n in news)

    def test_excludes_other_assets(self, db_session):
        asset_id = _make_asset(db_session)
        other_asset_id = _make_asset(db_session, asset_id=TEST_ASSET_ID_2, symbol="OTHER")
        db_session.add(
            NewsEvent(
                asset_id=other_asset_id,
                source="Finnhub",
                ts=AS_OF - timedelta(days=1),
                headline="non è il mio asset",
                url="https://example.com/other",
                fetched_at=datetime.now(timezone.utc),
            )
        )
        db_session.flush()

        news = get_recent_news(db_session, asset_id, as_of=AS_OF, days_back=7, max_items=20)

        assert news == []


class TestGetRecentCompanyEvents:
    def test_filters_by_window_and_asset(self, db_session):
        asset_id = _make_asset(db_session)
        db_session.add(
            CompanyEvent(
                asset_id=asset_id,
                ts=AS_OF.date() - timedelta(days=30),
                event_type="earnings",
                source="Finnhub",
                fetched_at=datetime.now(timezone.utc),
            )
        )
        db_session.add(
            CompanyEvent(
                asset_id=asset_id,
                ts=AS_OF.date() - timedelta(days=200),
                event_type="dividend",
                source="FMP",
                fetched_at=datetime.now(timezone.utc),
            )
        )
        db_session.flush()

        events = get_recent_company_events(db_session, asset_id, as_of=AS_OF, days_back=90)

        assert len(events) == 1
        assert events[0].event_type == "earnings"


class TestGetLatestMacroEvents:
    def test_returns_most_recent_value_per_indicator(self, db_session):
        db_session.add_all(
            [
                MacroEvent(
                    indicator="TESTIND1",
                    ts=date(2026, 7, 1),
                    value=4.0,
                    source="FRED",
                    fetched_at=datetime.now(timezone.utc),
                ),
                MacroEvent(
                    indicator="TESTIND1",
                    ts=date(2026, 8, 1),
                    value=4.1,
                    source="FRED",
                    fetched_at=datetime.now(timezone.utc),
                ),
                MacroEvent(
                    indicator="TESTIND2",
                    ts=date(2026, 8, 1),
                    value=310.0,
                    source="FRED",
                    fetched_at=datetime.now(timezone.utc),
                ),
            ]
        )
        db_session.flush()

        latest = get_latest_macro_events(db_session, as_of=AS_OF)
        by_indicator = {m.indicator: m.value for m in latest}

        assert by_indicator["TESTIND1"] == 4.1
        assert by_indicator["TESTIND2"] == 310.0

    def test_respects_no_look_ahead(self, db_session):
        db_session.add(
            MacroEvent(
                indicator="TESTIND3",
                ts=date(2026, 10, 1),
                value=99.9,
                source="FRED",
                fetched_at=datetime.now(timezone.utc),
            )
        )
        db_session.flush()

        latest = get_latest_macro_events(db_session, as_of=AS_OF)

        assert "TESTIND3" not in {m.indicator for m in latest}


class TestGetDecisionUniverse:
    def test_excludes_benchmark(self, db_session):
        asset_id = _make_asset(db_session, symbol="TESTX")
        benchmark_id = _make_asset(db_session, asset_id=TEST_ASSET_ID_2, symbol="TESTBENCH")
        db_session.add_all(
            [
                UniverseMember(
                    asset_id=asset_id,
                    is_benchmark=False,
                    source="universe-csv",
                    fetched_at=datetime.now(timezone.utc),
                ),
                UniverseMember(
                    asset_id=benchmark_id,
                    is_benchmark=True,
                    source="universe-csv",
                    fetched_at=datetime.now(timezone.utc),
                ),
            ]
        )
        db_session.flush()

        universe = get_decision_universe(db_session)
        symbols = {a.symbol for a in universe if a.asset_id in (asset_id, benchmark_id)}

        assert "TESTX" in symbols
        assert "TESTBENCH" not in symbols
