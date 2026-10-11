"""Interfacce Pydantic del modulo `llm/`.

Distinte da `schemas/records.py`, che copre solo le sei interfacce di
ingestion: `Decision` non è un dato in ingresso da una fonte esterna, è
l'output strutturato del provider LLM.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


# La cadenza di default di un portfolio è 7 giorni (168 ore): un rinvio più
# lungo cadrebbe nel ciclo successivo, che ridecide comunque tutto.
MAX_RECHECK_HOURS = 167


class DeferralRequest(BaseModel):
    """Richiesta di rinvio: l'LLM chiede di rivalutare l'asset fra
    `recheck_after_hours` ore, dentro il ciclo in corso, invece di aspettare
    il ciclo successivo (es. una trimestrale o un dato macro imminenti). Non
    è tool-use e non sospende la decisione: la `Decision` che la contiene
    vale ed è eseguita subito, il ricontrollo sarà una decisione nuova.

    Il motore salva il momento del ricontrollo sulla decisione
    (`t_model_decisions.recheck_at`/`recheck_reason`) e il giro orario
    `run-due` lo riprende quando scade, con i dati già aggiornati dalle
    pipeline orarie: nessun refresh ad-hoc. Ore intere perché il giro è
    orario, una precisione al minuto non verrebbe rispettata.
    """

    # ge/le, non gt/lt: Gemini accetta "minimum"/"maximum" nel response_schema
    # ma non "exclusiveMinimum" (ValidationError lato SDK, scoperto in un test
    # end-to-end reale).
    recheck_after_hours: int = Field(ge=1, le=MAX_RECHECK_HOURS)
    reason: str


class Decision(BaseModel):
    """Decisione atomica BUY/SELL/HOLD prodotta da un `LLMProvider`.

    Rispecchia solo i campi che l'LLM produce direttamente in
    `decisions.t_model_decisions` — non `run_id`/`asset_id`/`ts`/
    `context_snapshot`, che `decision_engine/` aggiunge al momento della
    persistenza. `defer`, se presente, diventa il ricontrollo salvato sulla
    decisione (`DeferralRequest`).

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
