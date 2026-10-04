"""Test unitari di `Database` (`marketmind_db.database`) e di
`IngestionRunAudit` (`marketmind_db.audit`), senza Postgres: la session
factory è sostituita da un mock.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Connection, Engine

from marketmind_db.access import INGESTION, READ_ONLY
from marketmind_db.audit import IngestionRunAudit
from marketmind_db.database import Database, DatabaseSettings
from marketmind_db.models.market_data import MarketPrice
from marketmind_db.repository import TableRepository
from marketmind_db.run_context import track_ingestion_run


@pytest.fixture
def session(mocker):
    return mocker.MagicMock()


def _db(mocker, session, *, dry_run: bool = False, timeout_ms: int | None = 60_000, policy=INGESTION):
    db = Database(
        DatabaseSettings(url="postgresql+psycopg://x@y/z", statement_timeout_ms=timeout_ms),
        policy=policy,
        dry_run=dry_run,
    )
    mocker.patch.object(db, "session_factory", return_value=lambda: session)
    return db


def _executed_params(session) -> list[dict]:
    return [c.args[1] for c in session.execute.call_args_list if len(c.args) > 1]


class TestDatabaseSettings:
    def test_from_env_usa_get_database_url(self, mocker):
        mocker.patch(
            "marketmind_db.database.get_database_url", return_value="postgresql+psycopg://a@b/c"
        )

        settings = DatabaseSettings.from_env()

        assert settings.url == "postgresql+psycopg://a@b/c"
        assert settings.statement_timeout_ms == DatabaseSettings.DEFAULT_STATEMENT_TIMEOUT_MS


class TestSession:
    def test_commit_alla_fine(self, mocker, session):
        with _db(mocker, session).session():
            pass

        session.commit.assert_called_once()
        session.rollback.assert_not_called()
        session.close.assert_called_once()

    def test_rollback_su_eccezione_e_rilancia(self, mocker, session):
        with pytest.raises(RuntimeError):
            with _db(mocker, session).session():
                raise RuntimeError("boom")

        session.rollback.assert_called_once()
        session.commit.assert_not_called()
        session.close.assert_called_once()

    def test_dry_run_committa_le_sessioni_ma_annulla_la_transazione_esterna(self, mocker):
        """In dry run le sessioni committano normalmente (diventano SAVEPOINT
        di un'unica transazione esterna): la riga di audit di un run deve
        restare visibile alle scritture successive, che la referenziano via
        FK. È la transazione esterna a essere annullata, alla chiusura."""
        engine = mocker.MagicMock(spec=Engine)
        connection = engine.connect.return_value
        connection.in_transaction.return_value = False
        db = Database(DatabaseSettings(url="x", statement_timeout_ms=None), policy=INGESTION, dry_run=True, bind=engine)
        session = mocker.MagicMock()
        mocker.patch.object(db, "session_factory", return_value=lambda: session)

        with db:
            with db.session():
                pass
            assert db.bind is connection
            session.commit.assert_called_once()
            connection.begin.return_value.rollback.assert_not_called()

        connection.begin.return_value.rollback.assert_called_once()
        connection.close.assert_called_once()

    def test_dry_run_su_connessione_gia_in_transazione_usa_un_savepoint(self, mocker):
        connection = mocker.MagicMock(spec=Connection)
        connection.in_transaction.return_value = True
        db = Database(DatabaseSettings(url="x"), policy=INGESTION, dry_run=True, bind=connection)

        assert db.bind is connection
        db.close()

        connection.begin_nested.return_value.rollback.assert_called_once()
        connection.close.assert_not_called()

    def test_close_senza_dry_run_non_fa_nulla(self, mocker, session):
        db = _db(mocker, session)
        db.close()

    def test_imposta_statement_timeout_locale(self, mocker, session):
        with _db(mocker, session, timeout_ms=1500).session():
            pass

        assert {"name": "statement_timeout", "value": "1500"} in _executed_params(session)

    def test_nessun_timeout_se_none(self, mocker, session):
        with _db(mocker, session, timeout_ms=None).session():
            pass

        session.execute.assert_not_called()

    def test_applica_il_run_id_tracciato(self, mocker, session):
        with track_ingestion_run(9), _db(mocker, session, timeout_ms=None).session():
            pass

        assert _executed_params(session) == [
            {"name": "marketmind.ingestion_run_id", "value": "9"}
        ]


class TestTransaction:
    def test_repository_con_la_policy_del_database(self, mocker, session):
        with _db(mocker, session, policy=READ_ONLY).transaction() as tx:
            repo = tx.repository(MarketPrice)

        assert isinstance(repo, TableRepository)
        assert repo.policy is READ_ONLY
        assert repo.model is MarketPrice

    def test_repository_riusato_nella_stessa_transazione(self, mocker, session):
        with _db(mocker, session).transaction() as tx:
            assert tx.repository(MarketPrice) is tx.repository(MarketPrice)


class TestIngestionRunAudit:
    @pytest.fixture
    def db(self, mocker):
        db = mocker.MagicMock(spec=Database)
        self.tx = mocker.MagicMock()
        db.transaction.return_value.__enter__.return_value = self.tx
        self.repo = self.tx.repository.return_value
        self.repo.insert_returning.return_value = [{"run_id": 11}]
        return db

    def _final_values(self) -> dict:
        return self.repo.update.call_args.args[0]

    def test_successo(self, db):
        with IngestionRunAudit(db, "fred", "market_data.t_macro_events") as run:
            run.rows_written += 3

        assert run.run_id == 11
        inserted = self.repo.insert_returning.call_args.args[0][0]
        assert inserted["status"] == "running"
        assert inserted["source"] == "fred"
        assert self._final_values()["status"] == "success"
        assert self._final_values()["rows_written"] == 3
        assert self.repo.update.call_args.kwargs["where"] == {"run_id": 11}

    def test_eccezione_marca_failed_e_rilancia(self, db):
        with pytest.raises(ValueError, match="boom"):
            with IngestionRunAudit(db, "fred", "market_data.t_macro_events"):
                raise ValueError("boom")

        assert self._final_values()["status"] == "failed"
        assert self._final_values()["error_message"] == "boom"

    def test_esito_partial_esplicito(self, db):
        with IngestionRunAudit(db, "fred", "market_data.t_macro_events") as run:
            run.mark_partial("1 indicatore su 4 fallito")

        assert self._final_values()["status"] == "partial"
        assert self._final_values()["error_message"] == "1 indicatore su 4 fallito"

    def test_esito_failed_esplicito_senza_eccezione(self, db):
        with IngestionRunAudit(db, "fred", "market_data.t_macro_events") as run:
            run.mark_failed("tutti gli indicatori falliti")

        assert self._final_values()["status"] == "failed"

    def test_messaggio_troncato(self, db):
        with pytest.raises(ValueError):
            with IngestionRunAudit(db, "fred", "market_data.t_macro_events"):
                raise ValueError("x" * 5000)

        assert len(self._final_values()["error_message"]) == IngestionRunAudit.MAX_ERROR_LENGTH

    def test_run_id_tracciato_solo_dentro_il_blocco(self, db, mocker):
        from marketmind_db import run_context

        with IngestionRunAudit(db, "fred", "market_data.t_macro_events"):
            assert run_context.current_ingestion_run_id() == 11
        assert run_context.current_ingestion_run_id() is None
