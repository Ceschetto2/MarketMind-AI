"""Eccezioni del modulo `llm/`."""

from __future__ import annotations


class DecisionError(Exception):
    """Un provider LLM non è riuscito a produrre una `Decision` valida.

    Sollevata quando la chiamata al provider fallisce anche dopo i retry, o
    quando la risposta non valida contro `llm.schemas.Decision`. Chi chiama
    `LLMProvider.decide()` (`decision_engine/`) la intercetta asset per
    asset: un fallimento resta un buco esplicito nello storico delle
    decisioni per quel run, non una decisione inventata — coerente con lo
    `status='partial'` già usato dalle pipeline di ingestion per lo stesso
    scopo.
    """
