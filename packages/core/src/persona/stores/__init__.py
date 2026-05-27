"""Memory stores — protocol, versioning, concrete typed stores, Chroma backend."""

from __future__ import annotations

from persona.stores.base import TypedStore
from persona.stores.chroma import CHROMA_QUERY_BATCH_CAP, ChromaBackend
from persona.stores.embedder import Embedder, SentenceTransformerEmbedder
from persona.stores.episodic import EpisodicStore
from persona.stores.errors import (
    AuditWriteError,
    BrokenVersionChainError,
    PersonaSelfWriteForbiddenError,
    RuntimeWriteForbiddenError,
    StoreNotFoundError,
)
from persona.stores.identity import IdentityStore
from persona.stores.policy import (
    PersonaSelfRequirement,
    PolicyDecision,
    PolicyRule,
    PolicyTable,
)
from persona.stores.protocol import MemoryStore
from persona.stores.self_facts import SelfFactsStore
from persona.stores.versioning import (
    compute_next_version,
    current_version,
    link_supersedes,
    validate_chain,
)
from persona.stores.worldview import WorldviewStore

__all__ = [
    "CHROMA_QUERY_BATCH_CAP",
    "AuditWriteError",
    "BrokenVersionChainError",
    "ChromaBackend",
    "Embedder",
    "EpisodicStore",
    "IdentityStore",
    "MemoryStore",
    "PersonaSelfRequirement",
    "PersonaSelfWriteForbiddenError",
    "PolicyDecision",
    "PolicyRule",
    "PolicyTable",
    "RuntimeWriteForbiddenError",
    "SelfFactsStore",
    "SentenceTransformerEmbedder",
    "StoreNotFoundError",
    "TypedStore",
    "WorldviewStore",
    "compute_next_version",
    "current_version",
    "link_supersedes",
    "validate_chain",
]
