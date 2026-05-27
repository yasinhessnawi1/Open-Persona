"""Re-exports of store-related domain exceptions.

Single source of truth for the exception classes is :mod:`persona.errors`.
This module exists so callers can ``from persona.stores.errors import ...``
without reaching into the package root for everything.
"""

from __future__ import annotations

from persona.errors import (
    AuditWriteError,
    BrokenVersionChainError,
    PersonaSelfWriteForbiddenError,
    RuntimeWriteForbiddenError,
    StoreNotFoundError,
)

__all__ = [
    "AuditWriteError",
    "BrokenVersionChainError",
    "PersonaSelfWriteForbiddenError",
    "RuntimeWriteForbiddenError",
    "StoreNotFoundError",
]
