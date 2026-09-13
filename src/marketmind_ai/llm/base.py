"""Interfaccia generica di provider LLM.

Un `Protocol`, non una classe astratta: nessuno stato condiviso da
ereditare, solo un contratto strutturale. Nuovi provider si aggiungono come
moduli affiancati (`llm/<provider>.py`) senza toccare `decision_engine/`,
che dipende solo da questa interfaccia.

Il tipo del `context` resta un `dict` generico per ora: la forma tipizzata
del "context package" (prezzi/news/eventi macro/eventi aziendali) è
demandata alla progettazione dell'Historical Context Builder in
`decision_engine/`, non ancora fatta.
"""

from __future__ import annotations

from typing import Any, Protocol

from marketmind_ai.llm.schemas import Decision, WatchlistSelection


class LLMProvider(Protocol):
    def decide(self, context: dict[str, Any]) -> Decision:
        """Produce una `Decision` a partire da un context package.

        Solleva `llm.exceptions.DecisionError` se non riesce a ottenere una
        `Decision` valida, anche dopo eventuali retry interni — mai un
        fallback silenzioso travestito da decisione vera.
        """
        ...

    def select_watchlist(self, context: dict[str, Any]) -> WatchlistSelection:
        """Sceglie quali asset dell'universo osservare per un portfolio,
        dato l'universo intero e la sua strategia — il bootstrap
        (`decision_engine.engine.initialize_portfolio`), non il loop
        settimanale ordinario.

        Solleva `llm.exceptions.DecisionError` alle stesse condizioni di
        `decide()`.
        """
        ...
