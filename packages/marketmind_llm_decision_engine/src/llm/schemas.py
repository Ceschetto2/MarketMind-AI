"""Interfacce Pydantic del modulo `llm/`.

Distinte da `schemas/records.py`, che copre solo le sei interfacce di
ingestion: `Decision` non è un dato in ingresso da una fonte esterna, è
l'output strutturato del provider LLM.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


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

    # ge=1, non gt=0: gt genera "exclusiveMinimum" nel JSON Schema, non
    # supportato dal sottoinsieme che Gemini accetta come response_schema
    # (ValidationError lato SDK, scoperto in un test end-to-end reale) — per
    # un intero positivo ge=1 è equivalente e genera "minimum", supportato.
    retry_after_minutes: int = Field(ge=1)
    refresh_pipeline: Optional[str] = None
    refresh_symbol: Optional[str] = None


class Decision(BaseModel):
    """Decisione atomica BUY/SELL/HOLD prodotta da un `LLMProvider`.

    Rispecchia solo i campi che l'LLM produce direttamente in
    `decisions.t_model_decisions` — non `run_id`/`asset_id`/`ts`/
    `context_snapshot`, che `decision_engine/` aggiunge al momento della
    persistenza. Dove persistere `defer`, se lo si persiste, è la stessa
    domanda ancora aperta.

    `size_pct` è la size del trade, proposta dall'LLM stesso (non una
    regola deterministica calcolata a valle): percentuale (0-1) del cash
    disponibile da investire su un `BUY`, o della posizione corrente da
    liquidare su un `SELL` — l'LLM ha già cash/posizioni nel context
    package (`decision_engine.schemas.PortfolioState`), quindi può
    proporre una size coerente con lo stato del portfolio. Obbligatorio
    per `BUY`/`SELL`, vietato per `HOLD` (nessuna size ha senso se non si
    fa nulla) — un `model_validator` lo impone qui, non lato API: Gemini
    non genera schema JSON condizionali da un vincolo cross-field.
    """

    decision: Literal["BUY", "SELL", "HOLD"]
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    reasoning: Optional[str] = None
    size_pct: Optional[float] = Field(default=None, ge=0, le=1)
    defer: Optional[DeferralRequest] = None

    @model_validator(mode="after")
    def _validate_size_pct(self) -> "Decision":
        if self.decision in ("BUY", "SELL") and self.size_pct is None:
            raise ValueError(f"size_pct è richiesto quando decision={self.decision!r}")
        if self.decision == "HOLD" and self.size_pct is not None:
            raise ValueError("size_pct non ha senso quando decision='HOLD'")
        return self


class WatchlistSelection(BaseModel):
    """Output del bootstrap di un portfolio: quali asset dell'universo
    osservato tenere d'occhio, dati l'universo intero e la strategia del
    portfolio (`decision_engine.engine.initialize_portfolio`).

    Non è una `Decision`: non c'è un BUY/SELL/HOLD, solo una selezione di
    scope — nessun capitale viene impegnato qui, `decision_engine/` la
    traduce in righe di `portfolio.t_portfolio_watchlist`, mai in
    `t_portfolio_positions`.
    """

    symbols: list[str]
    reasoning: Optional[str] = None
