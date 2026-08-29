"""Modelli ORM SQLAlchemy, un modulo per schema Postgres.

Importare questo package popola `Base.metadata` con tutte le tabelle: è il
modulo che `alembic/env.py` importa per l'autogenerate e che qualunque
script deve importare prima di usare `Base.metadata.create_all(...)` nei
test.
"""

from __future__ import annotations

from marketmind_ai.db.models.audit import AuditLog, IngestionRun
from marketmind_ai.db.models.decisions import BacktestResult, ModelDecision, ModelRun
from marketmind_ai.db.models.market_data import (
    Asset,
    CompanyEvent,
    MacroEvent,
    MarketPrice,
    NewsEvent,
)

__all__ = [
    "Asset",
    "MarketPrice",
    "NewsEvent",
    "MacroEvent",
    "CompanyEvent",
    "ModelRun",
    "ModelDecision",
    "BacktestResult",
    "IngestionRun",
    "AuditLog",
]
