"""Interfacce Pydantic del modulo `llm/`.

Distinte da `schemas/records.py`, che copre solo le sei interfacce di
ingestion: `Decision` non è un dato in ingresso da una fonte esterna, è
l'output strutturato del provider LLM.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class DeferralRequest(BaseModel):
    """Richiesta di rinvio: l'LLM chiede di essere ricontrollato più avanti
    invece di aspettare la prossima cadenza settimanale ordinaria (es. un
    annuncio o un evento macro imminente rendono la finestra attuale poco
    informativa). Non è tool-use: la `Decision` che la contiene si completa
    comunque nello stesso turno, la richiesta viene solo annotata ed
    elaborata dopo, in modo asincrono, da `orchestration/`.

    Il ritardo è relativo (`retry_after_minutes`), non un timestamp
    assoluto: si passa direttamente a `--on-active` di `systemd-run`, il
    meccanismo di scheduling scelto (dettaglio in
    `Market Mind AI - Docs/Decision Engine/01_rinvio_decisione_e_orchestration.md`).
    `refresh_pipeline`/`refresh_symbol` sono suggerimenti opzionali su cosa
    aggiornare prima di ridecidere — quali parametri ciascuna pipeline
    accetterà davvero resta da definire pipeline per pipeline.
    """

    retry_after_minutes: int = Field(gt=0)
    refresh_pipeline: Optional[str] = None
    refresh_symbol: Optional[str] = None


class Decision(BaseModel):
    """Decisione atomica BUY/SELL/HOLD prodotta da un `LLMProvider`.

    Rispecchia solo i campi che l'LLM produce direttamente in
    `decisions.t_model_decisions` — non `run_id`/`asset_id`/`ts`/
    `context_snapshot`, che `decision_engine/` aggiunge al momento della
    persistenza. Dove persistere `defer`, se lo si persiste, è la stessa
    domanda ancora aperta.
    """

    decision: Literal["BUY", "SELL", "HOLD"]
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    reasoning: Optional[str] = None
    defer: Optional[DeferralRequest] = None
