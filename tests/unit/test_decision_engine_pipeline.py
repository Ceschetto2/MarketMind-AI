"""Test unitario per l'entry point standalone `decision_engine/pipeline.py`.

`run()` è un thin wrapper attorno a `run_due_decisions()`, già testata a
fondo in `test_engine.py` — qui si verifica solo il collegamento.
"""

from __future__ import annotations

from marketmind_ai.decision_engine.pipeline import run


def test_run_delegates_to_run_due_decisions(mocker):
    mock_run_due_decisions = mocker.patch(
        "marketmind_ai.decision_engine.pipeline.run_due_decisions"
    )

    run()

    mock_run_due_decisions.assert_called_once_with()
