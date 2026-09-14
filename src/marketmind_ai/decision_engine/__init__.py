"""Historical Context Builder e loop di decisione BUY/SELL/HOLD, per portfolio.

Dipende solo dall'interfaccia generica di `llm/` (`LLMProvider`/`Decision`/
`DecisionError`/`get_provider`), mai da un'implementazione di provider
specifica — è il portfolio a scegliere provider e modello, non il
chiamante. Le query/scritture verso Postgres passano da `db/`
(`context_reader.py`, `portfolio_reader.py`, `portfolio_writer.py`,
`decision_writer.py`), mai da SQLAlchemy costruito qui dentro. Un run è
sempre per un solo portfolio: nessuna funzione qui legge mai lo stato di
più di un portfolio alla volta.

Un portfolio 'model' va inizializzato (`initialize_portfolio()`) prima che
`run_weekly_decisions()` produca decisioni per lui: sceglie lo scope di
asset da osservare, non ancora un entry point invocato da qui — pensata
per essere richiamata da un front-end futuro alla creazione/attivazione di
un portfolio.
"""

from marketmind_ai.decision_engine.context_builder import build_bootstrap_context, build_context
from marketmind_ai.decision_engine.engine import initialize_portfolio, run_weekly_decisions
from marketmind_ai.decision_engine.schemas import BootstrapContext, DecisionContext

__all__ = [
    "BootstrapContext",
    "DecisionContext",
    "build_bootstrap_context",
    "build_context",
    "initialize_portfolio",
    "run_weekly_decisions",
]
