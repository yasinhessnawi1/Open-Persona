"""Configuration for persona-core, loaded from environment variables.

All runtime knobs for the open-source library go here. Twelve-Factor: env vars
only, no YAML configuration files for runtime knobs (Hydra-style configs stay
out of product code; see ENGINEERING_STANDARDS.md §2.1).

Values are read once at process start via Pydantic Settings. Downstream code
should accept a ``PersonaCoreConfig`` instance through dependency injection
rather than read environment variables directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["PersonaCoreConfig"]


class PersonaCoreConfig(BaseSettings):
    """Environment-driven configuration for persona-core.

    Attributes:
        backend: Identifier of the chosen model backend (set in spec 02).
        api_key: API key for the chosen backend; never logged.
        model: Model identifier within the backend.
        chroma_path: Filesystem root for ChromaDB persistence and the default
            location of the JSONL audit log subdirectory.
        log_level: Minimum log level for the loguru sinks. Standard names
            (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, ``CRITICAL``).
        log_format: ``pretty`` for colored, human-readable output (CLI default);
            ``json`` for JSON Lines suitable for shipping to a log aggregator.
        log_file: Optional file path to add as an additional log sink. Both
            stderr and this file receive the same records when set.
        audit_path: Optional override for the audit-log directory. When
            unset, audit files live at ``<chroma_path>/audit/<persona_id>.jsonl``
            (per D-01-6).
    """

    model_config = SettingsConfigDict(env_prefix="PERSONA_", extra="ignore")

    backend: str = "anthropic"
    api_key: str = Field(default="", repr=False)
    model: str = "claude-sonnet-4-6"
    chroma_path: Path = Path(".chroma/")
    log_level: str = "INFO"
    log_format: Literal["pretty", "json"] = "pretty"
    log_file: Path | None = None
    audit_path: Path | None = None
