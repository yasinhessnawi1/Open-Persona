"""Persona schema — the v1.0 YAML model.

Mirrors architecture §4.2 and spec 01 §4. Every model is frozen + extra=forbid
so a typo in a persona YAML is caught at load time, not three layers deep
during a chat turn (fail-fast principle, ENGINEERING_STANDARDS.md §1.2).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — Pydantic needs runtime access
from pathlib import Path  # noqa: TC003 — Pydantic needs runtime access
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from yaml import YAMLError

from persona.errors import PersonaError, PersonaNotFoundError, SchemaVersionMismatchError

__all__ = [
    "SUPPORTED_SCHEMA_VERSIONS",
    "EmbeddingConfig",
    "EpisodicEntry",
    "Persona",
    "PersonaIdentity",
    "RoutingConfig",
    "SelfFact",
    "WorldviewClaim",
]

SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({"1.0"})


class PersonaIdentity(BaseModel):
    """Who the persona is. Immutable at runtime; edit the YAML to change.

    Attributes:
        name: Display name (e.g., ``"Astrid"``).
        role: One-line role description.
        background: Multi-line background paragraph. Goes into the system
            prompt verbatim.
        language_default: ISO 639-1 code (``"en"``, ``"nb"``, ...).
        constraints: Hard constraints the persona must honour. Each entry
            is one constraint sentence.
        visual_style: Optional persona-level aesthetic descriptor consumed
            by the Spec 15 image-generation merge (D-15-4). Additive per
            D-01-12 / D-13-X-now — existing personas without the field are
            byte-for-byte unaffected. Merges at generation time, NOT at
            prompt-build time; the runtime prompt builder does not read it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    background: str = Field(min_length=1)
    language_default: str = "en"
    constraints: list[str] = Field(default_factory=list)
    visual_style: str | None = None


class SelfFact(BaseModel):
    """A fact the persona holds about itself.

    Confidence is bounded to ``[0, 1]``. The store enforces the persona_self
    write threshold (D-01-5) against this value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    fact: str = Field(min_length=1)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class WorldviewClaim(BaseModel):
    """A claim the persona holds about the world.

    The store enforces that persona_self writes always set ``epistemic`` —
    the default of ``"belief"`` covers author-time writes via the YAML.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim: str = Field(min_length=1)
    domain: str = ""
    epistemic: Literal["fact", "belief", "hypothesis", "contested"] = "belief"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    valid_time: str = "always"


class EpisodicEntry(BaseModel):
    """A pre-loaded episodic memory (rare — episodic is usually runtime-written)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str = Field(min_length=1)
    created_at: datetime
    importance: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("created_at", mode="after")
    @classmethod
    def _created_at_must_be_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            msg = "naive datetime not allowed on EpisodicEntry.created_at"
            raise ValueError(msg)
        return value


class RoutingConfig(BaseModel):
    """Per-persona overrides for the runtime router (spec 05)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tier_for_generation: Literal["frontier", "mid", "small", "auto"] = "auto"
    tier_for_tools: Literal["frontier", "mid", "small", "auto"] = "small"


class EmbeddingConfig(BaseModel):
    """Embedder identity baked into the persona schema.

    Recording the embedder in the persona means a mismatch between the
    embedder used at index time and at query time is impossible by accident.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = "bge-small-en-v1.5"
    dim: int = Field(default=384, gt=0)


class Persona(BaseModel):
    """A v1.0 persona document.

    See spec 01 §4.1. Constructed in two paths:
    - From a YAML file via :meth:`from_yaml` (the common case).
    - Directly via ``Persona(...)`` in tests.

    Attributes:
        persona_id: Stable identifier. Derived from the YAML filename's stem
            via :meth:`from_yaml` when absent from the document.
        schema_version: Must match a value in
            :data:`SUPPORTED_SCHEMA_VERSIONS`; older/newer versions raise
            :class:`SchemaVersionMismatchError` at load time.
        owner_id: Set at runtime by the hosted API; absent in author-time YAMLs.
        visibility: ``private`` for v0.1; ``unlisted``/``public`` are
            reserved for the post-September registry.
        identity: Required.
        self_facts: List of author-time self-facts. May be empty.
        worldview: List of author-time worldview claims. May be empty.
        episodic: Pre-loaded episodic memories. Empty in the common case
            because episodic grows at runtime.
        tools: Allow-list of tool names (spec 03 enforces).
        skills: List of declared skill pack names (spec 04 binds).
        routing: Per-persona router overrides.
        embedding: Embedder identity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    persona_id: str | None = None
    schema_version: str = "1.0"
    owner_id: str | None = None
    visibility: Literal["private", "unlisted", "public"] = "private"

    identity: PersonaIdentity
    self_facts: list[SelfFact] = Field(default_factory=list)
    worldview: list[WorldviewClaim] = Field(default_factory=list)
    episodic: list[EpisodicEntry] = Field(default_factory=list)

    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)

    @field_validator("schema_version", mode="after")
    @classmethod
    def _schema_version_supported(cls, value: str) -> str:
        if value not in SUPPORTED_SCHEMA_VERSIONS:
            supported = ", ".join(sorted(SUPPORTED_SCHEMA_VERSIONS))
            msg = (
                f"unsupported schema_version {value!r}; this build supports "
                f"{{{supported}}}. See migrations in docs/specs/ for upgrade paths."
            )
            raise ValueError(msg)
        return value

    @classmethod
    def from_yaml(cls, path: Path) -> Persona:
        """Load and validate a v1.0 persona YAML.

        ``persona_id`` is derived from ``path.stem`` when the YAML omits it.
        If the YAML declares its own ``persona_id``, the YAML wins (explicit
        beats implicit).

        Args:
            path: Path to a YAML file on disk.

        Returns:
            A validated, frozen :class:`Persona` instance.

        Raises:
            PersonaNotFoundError: ``path`` does not exist or is not a file.
            PersonaError: The file is not valid YAML.
            SchemaVersionMismatchError: ``schema_version`` is unsupported.
            pydantic.ValidationError: The YAML's shape does not match the
                schema. Bubbles up unchanged — Pydantic's error formatting
                is excellent and gives the user the path-into-the-document.
        """
        if not path.exists():
            raise PersonaNotFoundError(
                "persona YAML not found",
                context={"path": str(path)},
            )
        if not path.is_file():
            raise PersonaNotFoundError(
                "expected a file, not a directory",
                context={"path": str(path)},
            )
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except YAMLError as exc:
            raise PersonaError(
                "invalid YAML",
                context={"path": str(path), "reason": str(exc)[:120]},
            ) from exc
        if not isinstance(raw, dict):
            raise PersonaError(
                "persona YAML must be a mapping at the top level",
                context={"path": str(path), "actual_type": type(raw).__name__},
            )
        # Auto-derive persona_id from filename when absent. We do this *before*
        # construction because the model is frozen.
        if "persona_id" not in raw or raw["persona_id"] is None:
            raw["persona_id"] = path.stem
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            # Special-case the schema_version mismatch so callers get a
            # specific domain exception instead of a generic ValidationError.
            for err in exc.errors():
                if err["loc"] == ("schema_version",) and "unsupported" in str(err.get("msg", "")):
                    raise SchemaVersionMismatchError(
                        str(err["msg"]),
                        context={"path": str(path)},
                    ) from exc
            raise
