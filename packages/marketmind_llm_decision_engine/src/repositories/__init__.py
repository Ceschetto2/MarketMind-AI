"""Accesso ai dati del Decision Engine: un repository per area, tutti sopra
`TableRepository` (quindi con la `AccessPolicy` e i safeguard di
`marketmind_db`) e legati alla `Transaction` che ricevono.

Restituiscono istanze ORM o piccoli value object (`AssetRef`,
`PortfolioRef`, ...), mai sessioni: il chiamante decide i confini delle
transazioni.
"""

from marketmind_llm_decision_engine.repositories.backtest import BacktestRepository, PositionEvent
from marketmind_llm_decision_engine.repositories.decisions import DecisionRepository
from marketmind_llm_decision_engine.repositories.market_context import AssetRef, MarketContextRepository
from marketmind_llm_decision_engine.repositories.portfolio import (
    PortfolioRef,
    PortfolioRepository,
    PositionView,
)

__all__ = [
    "AssetRef",
    "BacktestRepository",
    "DecisionRepository",
    "MarketContextRepository",
    "PortfolioRef",
    "PortfolioRepository",
    "PositionEvent",
    "PositionView",
]
