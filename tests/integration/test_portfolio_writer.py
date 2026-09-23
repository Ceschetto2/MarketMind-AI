"""Test di integrazione per `db/portfolio_writer.py` — richiedono Postgres reale.

Usa la fixture `db_session` (rollback automatico a fine test), tranne
`TestExecuteTradeRunLinkage` che verifica `SET LOCAL`/i trigger per davvero
tramite `get_session()` (stesso motivo di `TestIngestionRun` in
`test_writer.py`: quel meccanismo vale sulla connessione reale, non su
quella con savepoint della fixture) — pulizia esplicita a fine test.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from marketmind_ai.db.models.decisions import ModelRun
from marketmind_ai.db.models.market_data import Asset
from marketmind_ai.db.models.portfolio import (
    Portfolio,
    PortfolioPosition,
    PortfolioPositionSnapshot,
    PortfolioSnapshot,
    PortfolioWatchlistEntry,
)
from marketmind_ai.db.portfolio_writer import execute_trade, write_watchlist
from marketmind_ai.db.session import get_session, track_model_run
from marketmind_ai.llm.schemas import Decision

pytestmark = pytest.mark.integration

TEST_PORTFOLIO_ID = -1
TEST_ASSET_ID = -1
AS_OF = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def _make_portfolio(session, cash: float = 100_000.0) -> int:
    portfolio = Portfolio(
        portfolio_id=TEST_PORTFOLIO_ID,
        name="test-portfolio",
        portfolio_type="model",
        starting_capital=100_000.0,
        cash=cash,
        equity_value=100_000.0,
        created_at=datetime.now(timezone.utc),
        is_active=True,
        llm_provider="gemini",
        model_version="gemini-3.6-flash",
        strategy_prompt="strategia di test",
    )
    session.add(portfolio)
    session.flush()
    return portfolio.portfolio_id


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


def _get_portfolio(session, portfolio_id: int) -> Portfolio:
    return session.execute(
        select(Portfolio).where(Portfolio.portfolio_id == portfolio_id)
    ).scalar_one()


def _get_position(session, portfolio_id: int, asset_id: int) -> PortfolioPosition | None:
    return session.execute(
        select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id,
            PortfolioPosition.asset_id == asset_id,
        )
    ).scalar_one_or_none()


class TestExecuteTradeBuy:
    def test_buy_creates_a_new_position_and_debits_cash(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=10_000.0)
        asset_id = _make_asset(db_session)
        decision = Decision(decision="BUY", size_pct=0.5)

        execute_trade(db_session, portfolio_id, asset_id, decision, price=100.0)

        portfolio = _get_portfolio(db_session, portfolio_id)
        position = _get_position(db_session, portfolio_id, asset_id)
        assert portfolio.cash == 5_000.0
        assert position.quantity == 50.0
        assert position.avg_price == 100.0

    def test_buy_on_existing_position_recomputes_weighted_avg_price(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=10_000.0)
        asset_id = _make_asset(db_session)
        db_session.add(
            PortfolioPosition(
                portfolio_id=portfolio_id,
                asset_id=asset_id,
                quantity=10.0,
                avg_price=100.0,
                updated_at=datetime.now(timezone.utc),
            )
        )
        db_session.flush()

        # +1000 cash di acquisto a 200/azione -> 5 nuove azioni
        execute_trade(db_session, portfolio_id, asset_id, Decision(decision="BUY", size_pct=0.1), price=200.0)

        position = _get_position(db_session, portfolio_id, asset_id)
        # (10*100 + 5*200) / 15 = 133.33...
        assert position.quantity == 15.0
        assert position.avg_price == pytest.approx(133.333, rel=1e-3)

    def test_zero_size_pct_is_a_noop(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=10_000.0)
        asset_id = _make_asset(db_session)

        execute_trade(db_session, portfolio_id, asset_id, Decision(decision="BUY", size_pct=0.0), price=100.0)

        portfolio = _get_portfolio(db_session, portfolio_id)
        assert portfolio.cash == 10_000.0
        assert _get_position(db_session, portfolio_id, asset_id) is None


class TestExecuteTradeSell:
    def test_partial_sell_reduces_quantity_and_credits_cash(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=1_000.0)
        asset_id = _make_asset(db_session)
        db_session.add(
            PortfolioPosition(
                portfolio_id=portfolio_id,
                asset_id=asset_id,
                quantity=10.0,
                avg_price=100.0,
                updated_at=datetime.now(timezone.utc),
            )
        )
        db_session.flush()

        execute_trade(db_session, portfolio_id, asset_id, Decision(decision="SELL", size_pct=0.5), price=120.0)

        portfolio = _get_portfolio(db_session, portfolio_id)
        position = _get_position(db_session, portfolio_id, asset_id)
        assert portfolio.cash == 1_000.0 + 5 * 120.0
        assert position.quantity == 5.0
        assert position.avg_price == 100.0

    def test_full_sell_removes_the_position(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=0.0)
        asset_id = _make_asset(db_session)
        db_session.add(
            PortfolioPosition(
                portfolio_id=portfolio_id,
                asset_id=asset_id,
                quantity=10.0,
                avg_price=100.0,
                updated_at=datetime.now(timezone.utc),
            )
        )
        db_session.flush()

        execute_trade(db_session, portfolio_id, asset_id, Decision(decision="SELL", size_pct=1.0), price=120.0)

        portfolio = _get_portfolio(db_session, portfolio_id)
        assert portfolio.cash == 1_200.0
        assert _get_position(db_session, portfolio_id, asset_id) is None

    def test_sell_with_no_position_is_a_noop(self, db_session):
        portfolio_id = _make_portfolio(db_session, cash=1_000.0)
        asset_id = _make_asset(db_session)

        execute_trade(db_session, portfolio_id, asset_id, Decision(decision="SELL", size_pct=1.0), price=120.0)

        portfolio = _get_portfolio(db_session, portfolio_id)
        assert portfolio.cash == 1_000.0
        assert _get_position(db_session, portfolio_id, asset_id) is None


class TestExecuteTradeHold:
    def test_hold_raises_value_error(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        asset_id = _make_asset(db_session)

        with pytest.raises(ValueError):
            execute_trade(db_session, portfolio_id, asset_id, Decision(decision="HOLD"), price=100.0)


class TestWriteWatchlist:
    def test_creates_one_row_per_asset(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        asset_id_1 = _make_asset(db_session, -1, "AAA")
        asset_id_2 = _make_asset(db_session, -2, "BBB")

        write_watchlist(db_session, portfolio_id, [asset_id_1, asset_id_2], added_at=AS_OF)

        rows = db_session.execute(
            select(PortfolioWatchlistEntry).where(
                PortfolioWatchlistEntry.portfolio_id == portfolio_id
            )
        ).scalars().all()
        assert {r.asset_id for r in rows} == {asset_id_1, asset_id_2}

    def test_replaces_existing_watchlist_instead_of_appending(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        asset_id_1 = _make_asset(db_session, -1, "AAA")
        asset_id_2 = _make_asset(db_session, -2, "BBB")
        write_watchlist(db_session, portfolio_id, [asset_id_1], added_at=AS_OF)

        write_watchlist(db_session, portfolio_id, [asset_id_2], added_at=AS_OF)

        rows = db_session.execute(
            select(PortfolioWatchlistEntry).where(
                PortfolioWatchlistEntry.portfolio_id == portfolio_id
            )
        ).scalars().all()
        assert {r.asset_id for r in rows} == {asset_id_2}

    def test_empty_selection_clears_the_watchlist(self, db_session):
        portfolio_id = _make_portfolio(db_session)
        asset_id_1 = _make_asset(db_session, -1, "AAA")
        write_watchlist(db_session, portfolio_id, [asset_id_1], added_at=AS_OF)

        write_watchlist(db_session, portfolio_id, [], added_at=AS_OF)

        rows = db_session.execute(
            select(PortfolioWatchlistEntry).where(
                PortfolioWatchlistEntry.portfolio_id == portfolio_id
            )
        ).scalars().all()
        assert rows == []


class TestPortfolioDeleteTrigger:
    """Fix di `alembic/versions/0013_fix_portfolio_delete_snapshot_trigger.py`:
    cancellare un portfolio ora funziona — prima falliva sempre, perché il
    trigger `AFTER DELETE` provava a fotografare una riga già rimossa
    (`CLAUDE.md` § Ambiguità aperte, bug verificato empiricamente durante
    il lavoro sul Backtesting Engine)."""

    def test_deleting_a_portfolio_no_longer_raises(self):
        """Il fix impedisce solo che il DELETE generi da sé una nuova riga
        di storico orfana (il meccanismo che falliva *sempre*, anche su un
        portfolio senza alcuno storico pregresso) — non rimuove il vincolo
        di integrità referenziale verso storico già esistente: quello va
        ancora cancellato esplicitamente prima, come qualunque altra FK.
        Lo storico qui esiste già di suo: il semplice INSERT sopra fa
        scattare il trigger e produce una riga in t_portfolio_snapshots."""
        portfolio_id = -1002
        with get_session() as session:
            session.add(
                Portfolio(
                    portfolio_id=portfolio_id,
                    name="test-delete-trigger-portfolio",
                    portfolio_type="benchmark",
                    starting_capital=1_000.0,
                    cash=1_000.0,
                    equity_value=1_000.0,
                    created_at=datetime.now(timezone.utc),
                    is_active=True,
                )
            )

        with get_session() as session:
            session.execute(
                delete(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == portfolio_id)
            )
            session.execute(delete(Portfolio).where(Portfolio.portfolio_id == portfolio_id))

        with get_session() as session:
            assert session.get(Portfolio, portfolio_id) is None


class TestExecuteTradeRunLinkage:
    """Fix di `Market Mind AI - Docs/tasks/2026-09-14-run-id-guc-linkage.md`:
    un trade eseguito dentro `track_model_run(run_id)` deve produrre snapshot
    con quel `run_id` popolato, non NULL — prima del fix, `SET LOCAL` non
    veniva mai impostato e ogni riga storica restava orfana.

    Usa `get_session()` per davvero (non la fixture `db_session`, che tiene
    la scrittura su un savepoint di una propria connessione): il
    meccanismo `SET LOCAL`/trigger va verificato sulla stessa connessione
    che la produzione userebbe. Id negativi dedicati (`RUN_PORTFOLIO_ID`/
    `RUN_ASSET_ID`), diversi da `TEST_PORTFOLIO_ID`/`TEST_ASSET_ID` usati
    dal resto del file (quelli restano dentro un savepoint mai committato
    per davvero): scrittura e pulizia reali non devono competere con quelli.

    Il portfolio di test viene cancellato per davvero a fine test, non solo
    riusato/resettato: da `0013_fix_portfolio_delete_snapshot_trigger` un
    DELETE su `t_portfolios` funziona (prima falliva sempre — v.
    `TestPortfolioDeleteTrigger` sopra). Uno storico da ripulire prima
    resta comunque necessario — il fix non introduce `ON DELETE CASCADE` —
    quindi l'ordine di cancellazione (posizioni, poi i due storici, poi il
    portfolio) resta lo stesso già seguito altrove in questo file.
    """

    RUN_PORTFOLIO_ID = -1001
    RUN_ASSET_ID = -1001

    def _make_portfolio(self, session) -> int:
        portfolio = Portfolio(
            portfolio_id=self.RUN_PORTFOLIO_ID,
            name="test-run-linkage-portfolio",
            portfolio_type="model",
            starting_capital=10_000.0,
            cash=10_000.0,
            equity_value=10_000.0,
            created_at=datetime.now(timezone.utc),
            is_active=True,
            llm_provider="gemini",
            model_version="gemini-3.6-flash",
            strategy_prompt="strategia di test",
        )
        session.add(portfolio)
        session.flush()
        return portfolio.portfolio_id

    def _make_asset(self, session) -> int:
        asset = Asset(
            asset_id=self.RUN_ASSET_ID,
            symbol="RUNLINK",
            name="Test Asset",
            sector="Test",
            asset_type="equity",
            source="yfinance",
            fetched_at=datetime.now(timezone.utc),
        )
        session.add(asset)
        session.flush()
        return asset.asset_id

    def _cleanup(self, run_id: int | None) -> None:
        # Ordine non arbitrario. Cancellare PortfolioPosition fa scattare
        # fn_portfolio_position_snapshot() (AFTER DELETE), che inserisce una
        # NUOVA riga di storico riferita allo stesso portfolio_id/asset_id —
        # va quindi cancellata *dopo*, altrimenti resta un tombstone
        # orfano che blocca la successiva DELETE su Asset (FK). ModelRun va
        # cancellato prima di Portfolio, non dopo: t_model_runs.portfolio_id
        # è una FK reale (NOT NULL) verso t_portfolios, non nullable come
        # quella sugli storici — Portfolio non si cancella finché un suo
        # run esiste ancora, indipendentemente dal fix di `0013`.
        with get_session() as session:
            session.execute(
                delete(PortfolioPosition).where(
                    PortfolioPosition.portfolio_id == self.RUN_PORTFOLIO_ID
                )
            )
            session.execute(
                delete(PortfolioPositionSnapshot).where(
                    PortfolioPositionSnapshot.portfolio_id == self.RUN_PORTFOLIO_ID
                )
            )
            session.execute(
                delete(PortfolioSnapshot).where(
                    PortfolioSnapshot.portfolio_id == self.RUN_PORTFOLIO_ID
                )
            )
            if run_id is not None:
                session.execute(delete(ModelRun).where(ModelRun.run_id == run_id))
            session.execute(
                delete(Portfolio).where(Portfolio.portfolio_id == self.RUN_PORTFOLIO_ID)
            )
            session.execute(delete(Asset).where(Asset.asset_id == self.RUN_ASSET_ID))

    def test_trade_inside_track_model_run_links_position_and_cash_snapshots(self):
        run_id = None
        try:
            with get_session() as session:
                portfolio_id = self._make_portfolio(session)
                asset_id = self._make_asset(session)
                run = ModelRun(
                    portfolio_id=portfolio_id,
                    ts=AS_OF,
                    config={},
                    llm_provider="gemini",
                    model_version="test",
                )
                session.add(run)
                session.flush()
                run_id = run.run_id

            with track_model_run(run_id):
                with get_session() as session:
                    execute_trade(
                        session,
                        portfolio_id,
                        asset_id,
                        Decision(decision="BUY", size_pct=0.5),
                        price=100.0,
                    )

            with get_session() as session:
                # Filtrate anche per run_id, non solo portfolio_id: la
                # creazione stessa del portfolio produce già uno snapshot
                # di cash con run_id NULL (INSERT fuori da track_model_run),
                # distinto da quello del trade tracciato.
                position_snapshot = session.execute(
                    select(PortfolioPositionSnapshot).where(
                        PortfolioPositionSnapshot.portfolio_id == portfolio_id,
                        PortfolioPositionSnapshot.run_id == run_id,
                    )
                ).scalar_one()
                cash_snapshot = session.execute(
                    select(PortfolioSnapshot).where(
                        PortfolioSnapshot.portfolio_id == portfolio_id,
                        PortfolioSnapshot.run_id == run_id,
                    )
                ).scalar_one()
                assert position_snapshot.run_id == run_id
                assert cash_snapshot.run_id == run_id
        finally:
            self._cleanup(run_id)

    def test_trade_outside_track_model_run_leaves_run_id_null(self):
        """Comportamento pre-fix, ancora valido come caso legittimo: un
        trade fuori da un run tracciato (nessuna chiamata a
        track_model_run) non deve inventare un collegamento."""
        with get_session() as session:
            portfolio_id = self._make_portfolio(session)
            asset_id = self._make_asset(session)

        try:
            with get_session() as session:
                execute_trade(
                    session,
                    portfolio_id,
                    asset_id,
                    Decision(decision="BUY", size_pct=0.5),
                    price=100.0,
                )

            with get_session() as session:
                position_snapshot = session.execute(
                    select(PortfolioPositionSnapshot).where(
                        PortfolioPositionSnapshot.portfolio_id == portfolio_id
                    )
                ).scalar_one()
                assert position_snapshot.run_id is None
        finally:
            self._cleanup(None)
