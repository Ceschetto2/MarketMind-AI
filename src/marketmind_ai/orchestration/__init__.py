"""Libreria condivisa per innescare azioni ad-hoc fuori dal ciclo ordinario
di ingestion/decisione — oggi solo il rinvio di una decisione LLM.

Nessuna delle otto pipeline di ingestion invoca questo modulo per il
proprio funzionamento ordinario (restano scatenate direttamente dal
proprio `.timer` systemd); è `decision_engine/` (non ancora scritto) a
dipendere da qui, e solo per il caso del rinvio. Dettaglio del disegno in
`Market Mind AI - Docs/Decision Engine/01_rinvio_decisione_e_orchestration.md`.
"""

from marketmind_ai.orchestration.exceptions import OrchestrationError
from marketmind_ai.orchestration.scheduler import (
    schedule_transient_run,
    trigger_ingestion_pipeline,
)

__all__ = [
    "OrchestrationError",
    "schedule_transient_run",
    "trigger_ingestion_pipeline",
]
