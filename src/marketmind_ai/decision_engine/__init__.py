"""Historical Context Builder e loop di decisione BUY/SELL/HOLD.

Dipende solo dall'interfaccia generica di `llm/` (`LLMProvider`/`Decision`/
`DecisionError`), mai da un'implementazione di provider specifica. Le
query/scritture verso Postgres passano da `db/` (`context_reader.py`,
`decision_writer.py`), mai da SQLAlchemy costruito qui dentro.
"""

from marketmind_ai.decision_engine.context_builder import build_context
from marketmind_ai.decision_engine.engine import run_weekly_decisions
from marketmind_ai.decision_engine.schemas import DecisionContext

__all__ = [
    "DecisionContext",
    "build_context",
    "run_weekly_decisions",
]
