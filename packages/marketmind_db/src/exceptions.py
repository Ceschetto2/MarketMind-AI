"""Eccezioni dello strato generico di accesso al DB.

Sollevate *prima* di eseguire qualunque statement: un safeguard violato è un
errore di programmazione del chiamante, da correggere nel codice, non una
condizione da gestire a runtime con un retry.
"""

from __future__ import annotations


class RepositoryError(Exception):
    """Base di tutte le eccezioni sollevate da `marketmind_db`."""


class AccessDeniedError(RepositoryError):
    """Scrittura su uno schema che la `AccessPolicy` del chiamante non ammette."""


class InvalidQueryError(RepositoryError, ValueError):
    """Richiesta non valida per la tabella: colonne inesistenti, chiave di
    conflitto che non corrisponde a un vincolo reale, `UPDATE`/`DELETE` senza
    filtro, SQL testuale, ecc."""
