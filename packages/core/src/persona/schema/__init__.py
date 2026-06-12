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
    CatalogueVoice,
    EmbeddingConfig,
    EpisodicEntry,
    Persona,
    PersonaIdentity,
    RoutingConfig,
    SelfFact,
    VoiceSpec,
    WorldviewClaim,
)
from persona.schema.skills import SkillSpec
from persona.schema.tools import Tool, ToolCall, ToolResult

__all__ = [
    "CHUNK_ID_INDEX_WIDTH",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CatalogueVoice",
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
    "VoiceSpec",
    "WorldviewClaim",
    "WriteSource",
    "make_chunk_id",
]
