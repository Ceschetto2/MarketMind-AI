"""Historical Context Builder e loop di decisione BUY/SELL/HOLD, per portfolio.

Dipende solo dall'interfaccia generica di `llm/` (`LLMProvider`/`Decision`/
`DecisionError`/`get_provider`), mai da un'implementazione di provider
specifica — è il portfolio a scegliere provider e modello, non il
chiamante. Le query/scritture verso Postgres passano da `db/`
(`context_reader.py`, `portfolio_reader.py`, `decision_writer.py`), mai da
SQLAlchemy costruito qui dentro. Un run è sempre per un solo portfolio:
nessuna funzione qui legge mai lo stato di più di un portfolio alla volta.
"""

from marketmind_ai.decision_engine.context_builder import build_context
from marketmind_ai.decision_engine.engine import run_weekly_decisions
from marketmind_ai.decision_engine.schemas import DecisionContext

__all__ = [
    "DecisionContext",
    "build_context",
    "run_weekly_decisions",
]
