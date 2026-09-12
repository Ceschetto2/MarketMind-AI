"""Interfacce Pydantic del modulo `llm/`.

Distinte da `schemas/records.py`, che copre solo le sei interfacce di
ingestion: `Decision` non è un dato in ingresso da una fonte esterna, è
l'output strutturato del provider LLM.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Decision(BaseModel):
    """Decisione atomica BUY/SELL/HOLD prodotta da un `LLMProvider`.

    Rispecchia solo i campi che l'LLM produce direttamente in
    `decisions.t_model_decisions` — non `run_id`/`asset_id`/`ts`/
    `context_snapshot`, che `decision_engine/` aggiunge al momento della
    persistenza.
    """

    decision: Literal["BUY", "SELL", "HOLD"]
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    reasoning: Optional[str] = None
