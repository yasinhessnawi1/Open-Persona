"""Pydantic schema models shared across persona-core.

Public re-exports — anything not in ``__all__`` is private. See
``docs/specs/spec_01/spec_01_core.md`` §4–§5.
"""

from __future__ import annotations

from persona.schema.chunks import (
    CHUNK_ID_INDEX_WIDTH,
    ChunkProvenance,
    PersonaChunk,
    WriteSource,
    make_chunk_id,
)
from persona.schema.conversation import (
    Conversation,
    ConversationHistory,
    ConversationMessage,
)
from persona.schema.persona import (
    SUPPORTED_SCHEMA_VERSIONS,
    EmbeddingConfig,
    EpisodicEntry,
    Persona,
    PersonaIdentity,
    RoutingConfig,
    SelfFact,
    WorldviewClaim,
)
from persona.schema.skills import SkillSpec
from persona.schema.tools import Tool, ToolCall, ToolResult

__all__ = [
    "CHUNK_ID_INDEX_WIDTH",
    "SUPPORTED_SCHEMA_VERSIONS",
    "ChunkProvenance",
    "Conversation",
    "ConversationHistory",
    "ConversationMessage",
    "EmbeddingConfig",
    "EpisodicEntry",
    "Persona",
    "PersonaChunk",
    "PersonaIdentity",
    "RoutingConfig",
    "SelfFact",
    "SkillSpec",
    "Tool",
    "ToolCall",
    "ToolResult",
    "WorldviewClaim",
    "WriteSource",
    "make_chunk_id",
]
