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

from pydantic import Field, SecretStr, field_validator
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
        web_search_provider: Search backend for the ``web_search`` tool
            (spec 03, D-03-9). ``brave`` is the only provider wired in v0.1;
            ``tavily`` and ``serpapi`` are stubs raising NotImplementedError.
        web_search_api_key: API key for ``web_search_provider`` (D-03-10).
            Read from ``PERSONA_WEB_SEARCH_API_KEY``; never logged.
        tools_sandbox_root: Per-CWD-relative sandbox root for the
            ``file_read``/``file_write`` tools (spec 03, D-03-23).
        currency_provider: Exchange-rate backend for the ``currency_convert``
            tool (spec 26, D-26-6). ``frankfurter`` (default, no key) and
            ``exchangerate_api`` (no key) are wired.
        currency_api_key: Optional API key for keyed currency providers
            (spec 26). Read from ``PERSONA_CURRENCY_API_KEY``; never logged.
        mcp_servers: Mapping of MCP server name → URL parsed from
            ``PERSONA_MCP_SERVERS`` (comma-separated ``name=url`` per
            D-03-22). Empty dict when the env var is unset.
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

    # Spec 03 — tools (T12).
    web_search_provider: Literal["brave", "tavily", "serpapi"] = "brave"
    web_search_api_key: SecretStr | None = None
    tools_sandbox_root: Path = Path("./.persona_work")
    # Spec 26 T04 — currency_convert. ``frankfurter`` (ECB-class reference
    # rates) is the default and needs NO key, so the tool works out of the box
    # on ``pip install`` (D-26-X-currency-no-key-default-rationale).
    # ``exchangerate_api`` is a no-key alternate. ``currency_api_key`` is only
    # consulted for keyed providers added later (provider-conditional guard,
    # D-26-6); never logged.
    currency_provider: Literal["frankfurter", "exchangerate_api"] = "frankfurter"
    currency_api_key: SecretStr | None = None
    # `mcp_servers` is stored as the raw comma-separated string so Pydantic
    # Settings doesn't try to JSON-parse the env value. The dict is computed
    # via `mcp_servers_parsed` below; downstream code uses that. D-03-22.
    mcp_servers: str = Field(default="", repr=False)

    @field_validator("mcp_servers", mode="after")
    @classmethod
    def _validate_mcp_servers(cls, value: str) -> str:
        """Validate the comma-separated `name=url` env format (D-03-22).

        Empty string is valid (no MCP servers configured). Otherwise each
        entry must contain exactly one ``=``, the name must match
        ``[A-Za-z0-9_-]+``, the URL must be ``http://`` or ``https://``.
        Duplicate names raise. The actual dict is exposed via
        :attr:`mcp_servers_parsed`.
        """
        if not value:
            return ""
        # Run the parser purely for its side-effect of raising on malformed input.
        _parse_mcp_servers_string(value)
        return value

    @property
    def mcp_servers_parsed(self) -> dict[str, str]:
        """Parsed mapping of MCP server name → URL (D-03-22)."""
        if not self.mcp_servers:
            return {}
        return _parse_mcp_servers_string(self.mcp_servers)


def _parse_mcp_servers_string(value: str) -> dict[str, str]:
    """Implementation of the D-03-22 parser used by validator + property."""
    import re

    result: dict[str, str] = {}
    for raw in value.split(","):
        entry = raw.strip()
        if not entry:
            continue
        if entry.count("=") < 1:
            msg = f"malformed PERSONA_MCP_SERVERS entry (missing '='): {entry!r}"
            raise ValueError(msg)
        name, url = entry.split("=", 1)
        name = name.strip()
        url = url.strip()
        if not name:
            msg = f"empty server name in PERSONA_MCP_SERVERS: {entry!r}"
            raise ValueError(msg)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            msg = (
                f"invalid server name in PERSONA_MCP_SERVERS: {name!r} (must match [A-Za-z0-9_-]+)"
            )
            raise ValueError(msg)
        if not url:
            msg = f"empty URL for server {name!r} in PERSONA_MCP_SERVERS"
            raise ValueError(msg)
        if not (url.startswith("http://") or url.startswith("https://")):
            msg = (
                f"invalid URL for MCP server {name!r}: "
                f"{url!r} (must start with http:// or https://)"
            )
            raise ValueError(msg)
        if name in result:
            msg = f"duplicate MCP server name in PERSONA_MCP_SERVERS: {name!r}"
            raise ValueError(msg)
        result[name] = url
    return result
