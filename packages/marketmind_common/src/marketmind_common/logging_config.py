"""Setup di logging condiviso — vedi
`Market Mind AI - Docs/Architettura/04_logging.md` per la convenzione
completa (livelli, cosa loggare, relazione con `audit.t_ingestion_runs`).

Ogni entry point standalone (script di ingestion, job di orchestration)
chiama `configure_logging()` una volta all'avvio, prima di fare qualunque
altra cosa. I moduli di libreria non la chiamano mai: si limitano a
`logging.getLogger(__name__)` e lasciano la configurazione degli handler a
chi li importa, per evitare handler duplicati quando un modulo viene
importato da più entry point (es. nei test).
"""

from __future__ import annotations

import logging

_FORMAT = "%(levelname)-5.5s [%(name)s] %(message)s"

# Namespace applicativi, uno per pacchetto del workspace (`packages/`): ogni
# modulo usa `logging.getLogger(__name__)`, quindi il logger radice di
# ciascun pacchetto è il nome del pacchetto stesso.
APP_LOGGER_NAMES = (
    "marketmind_common",
    "marketmind_db",
    "marketmind_pipelines",
    "marketmind_llm_decision_engine",
    "marketmind_frontend",
)


def configure_logging(level: int = logging.INFO) -> None:
    """Configura il root logger a `WARNING` (silenzia librerie terze come
    `requests`/`urllib3`/SQLAlchemy) e i namespace
    applicativi (`APP_LOGGER_NAMES`) al `level` indicato (default `INFO`).

    Idempotente sull'handler: chiamarla più volte non duplica l'output,
    anche se un entry point la richiamasse più di una volta.
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.WARNING, format=_FORMAT)
    for name in APP_LOGGER_NAMES:
        logging.getLogger(name).setLevel(level)
