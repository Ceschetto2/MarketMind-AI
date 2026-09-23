"""Entry point standalone del Decision Engine.

Invocato dal container Quadlet `marketmind-decide.container` (`Exec=python
-m marketmind_ai.decision_engine.pipeline`), a cadenza oraria via
`marketmind-decide.timer` — un solo timer condiviso, non uno per portfolio.
La cadenza per-portfolio vera e propria non vive nel timer: è il motore
stesso a deciderla (`t_portfolios.next_decision_at`,
`decision_engine.engine.run_due_decisions`), il timer si limita a
controllare più spesso del necessario "chi è scaduto ORA". Gira con le
credenziali del ruolo Postgres applicativo `marketmind_app` (scrittura
`decisions`/`portfolio`, lettura `market_data`), mai `marketmind_ingestion`.

Vedi `Market Mind AI - Docs/Decision Engine/05_timer_e_cadenza.md`.
"""

from __future__ import annotations

from marketmind_ai.decision_engine.engine import run_due_decisions
from marketmind_ai.utils.logging_config import configure_logging


def run() -> None:
    run_due_decisions()


if __name__ == "__main__":
    configure_logging()
    run()
