"""Test unitari per il collegamento `run_id` dei trigger via GUC di sessione
(`db/session.py`). Nessun accesso a Postgres: `_apply_run_context` è
testata direttamente contro una sessione mockata.
"""

from __future__ import annotations

from marketmind_ai.db.session import _apply_run_context, track_ingestion_run, track_model_run


class TestApplyRunContext:
    def test_noop_when_nothing_tracked(self, mocker):
        session = mocker.MagicMock()

        _apply_run_context(session)

        session.execute.assert_not_called()

    def test_sets_ingestion_run_id_guc_when_tracked(self, mocker):
        session = mocker.MagicMock()

        with track_ingestion_run(42):
            _apply_run_context(session)

        session.execute.assert_called_once()
        params = session.execute.call_args.args[1]
        assert params == {"name": "marketmind.ingestion_run_id", "value": "42"}

    def test_sets_model_run_id_guc_when_tracked(self, mocker):
        session = mocker.MagicMock()

        with track_model_run(7):
            _apply_run_context(session)

        session.execute.assert_called_once()
        params = session.execute.call_args.args[1]
        assert params == {"name": "marketmind.model_run_id", "value": "7"}

    def test_sets_both_gucs_when_both_tracked(self, mocker):
        """Non dovrebbe mai capitare in pratica (ingestion e decision engine
        non si annidano), ma il meccanismo resta indipendente per i due
        casi: entrambe le GUC vanno impostate se entrambe attive."""
        session = mocker.MagicMock()

        with track_ingestion_run(1), track_model_run(2):
            _apply_run_context(session)

        assert session.execute.call_count == 2

    def test_context_resets_after_exit(self, mocker):
        with track_ingestion_run(1):
            pass

        session = mocker.MagicMock()
        _apply_run_context(session)

        session.execute.assert_not_called()

    def test_nested_run_ids_restore_the_outer_value_on_exit(self, mocker):
        with track_model_run(1):
            with track_model_run(2):
                pass
            session = mocker.MagicMock()
            _apply_run_context(session)
            params = session.execute.call_args.args[1]
            assert params == {"name": "marketmind.model_run_id", "value": "1"}
