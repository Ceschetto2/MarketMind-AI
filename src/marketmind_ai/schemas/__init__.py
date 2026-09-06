"""Interfacce Pydantic condivise tra ingestion e strato di scrittura.

Si veda `records.py` per i docstring completi e
`Market Mind AI - Docs/Data Providers/00_schema_interfacce.md` per il
contratto sorgente.
"""

from marketmind_ai.schemas.records import (
    AssetRecord,
    CompanyEventRecord,
    MacroEventRecord,
    MarketPriceRecord,
    NewsEventRecord,
    UniverseMemberRecord,
)

__all__ = [
    "AssetRecord",
    "CompanyEventRecord",
    "MacroEventRecord",
    "MarketPriceRecord",
    "NewsEventRecord",
    "UniverseMemberRecord",
]
