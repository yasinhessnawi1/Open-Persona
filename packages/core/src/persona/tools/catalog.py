"""Known-tool catalog — the platform's tool vocabulary (spec 26 T08).

A single declarative enumeration of **every** built-in platform tool, including
the runtime-wired ones (``code_execution`` / ``generate_image`` /
``text_summarize``) whose factories need runtime context. This mirrors Spec
24's ``skills.toml`` declarative-catalog pattern, in code form (tools are
code-defined factories, so a typed Python module is the right analog and keeps
``runtime → core`` imports legal).

It is the shared vocabulary for:

- the authoring **tool recommender** (spec 26 T09) — the set of candidates it
  ranks, and the post-hoc filter that drops hallucinated tool names;
- the runtime **tool-gap detector** (spec 26 T10) — maps a "I can't do X"
  signal to a catalog tool the persona's allow-list lacks.

**No hard validation** of ``persona.tools`` against this catalog
(D-26-X-known-tool-catalog) — that would reject existing personas whose
free-form allow-lists predate the catalog (breaks backward-compat). The
strongest action is a soft ``WARNING`` log via :func:`warn_unknown_declared_tools`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from persona.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable

    from loguru import Logger

__all__ = [
    "TOOL_CATALOG",
    "ToolCatalogEntry",
    "catalog_entry",
    "known_tool_names",
    "warn_unknown_declared_tools",
]

_logger = get_logger("tools.catalog")

ToolCategory = Literal["web", "files", "compute", "text", "data", "datetime", "finance", "media"]


class ToolCatalogEntry(BaseModel):
    """One platform tool's catalog metadata (frozen).

    Attributes:
        name: The tool name as it appears in a persona's ``tools`` allow-list.
        description: A concise, authoring-facing summary of what the tool is for
            (distinct from the verbose, model-facing tool ``description``).
        category: Coarse grouping that helps the recommender reason by need.
        provider: Source of the tool. ``"builtin"`` for everything here; Spec 27
            adds ``"mcp"`` entries dynamically (not part of this static catalog).
        runtime_wired: True when the tool is composed by the runtime/API factory
            (needs a model / sandbox / image backend) rather than by
            ``build_default_toolbox``. Informational — the catalog lists it
            regardless so the recommender and gap-detector see the full surface.
        keywords: Capability phrases that map a "I can't do X" model utterance to
            this tool (spec 26 T10 gap-detection vocabulary). Lower-cased
            substrings matched against the model's output; empty disables
            gap-mapping for the tool.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    category: ToolCategory
    provider: Literal["builtin"] = "builtin"
    runtime_wired: bool = False
    keywords: tuple[str, ...] = ()


#: The authoritative platform tool list. Ordered roughly by category for
#: readability; consumers should not depend on order.
TOOL_CATALOG: tuple[ToolCatalogEntry, ...] = (
    # -- web (spec 03) --
    ToolCatalogEntry(
        name="web_search",
        description="Search the web for current information, news, and references.",
        category="web",
        keywords=("search the web", "look it up", "look that up", "browse the internet"),
    ),
    ToolCatalogEntry(
        name="web_fetch",
        description="Fetch a URL and extract its readable text content.",
        category="web",
        keywords=("open the link", "open that url", "fetch the page", "access the website"),
    ),
    # -- files (spec 03, sandboxed workspace) --
    ToolCatalogEntry(
        name="file_read",
        description="Read a file from the persona's sandboxed working directory.",
        category="files",
        keywords=("read the file", "open the file", "access the file"),
    ),
    ToolCatalogEntry(
        name="file_write",
        description="Write a file into the persona's sandboxed working directory.",
        category="files",
        keywords=("write a file", "save to a file", "save the file"),
    ),
    # -- compute --
    ToolCatalogEntry(
        name="code_execution",
        description="Run sandboxed Python for arbitrary computation, parsing, or data work.",
        category="compute",
        runtime_wired=True,
        keywords=("run code", "execute code", "run python", "run a script"),
    ),
    ToolCatalogEntry(
        name="calculator",
        description="Evaluate exact arithmetic and math-function expressions (no code sandbox).",
        category="compute",
        keywords=("calculate", "do the math", "compute that", "do arithmetic", "work out the"),
    ),
    # -- datetime (spec 26) --
    ToolCatalogEntry(
        name="datetime",
        description="Current time, timezone conversion, and date arithmetic.",
        category="datetime",
        keywords=("current time", "what time", "today's date", "timezone", "what's the date"),
    ),
    # -- text (spec 26) --
    ToolCatalogEntry(
        name="regex_match",
        description="Match, extract, or replace text with a regular expression.",
        category="text",
        keywords=("regular expression", "regex", "pattern match"),
    ),
    ToolCatalogEntry(
        name="text_diff",
        description="Show the line-by-line differences between two versions of a text.",
        category="text",
        keywords=("compare the text", "diff the", "what changed between", "show the differences"),
    ),
    ToolCatalogEntry(
        name="text_summarize",
        description="Condense a long passage into a short, on-voice summary.",
        category="text",
        runtime_wired=True,
        keywords=("summarize", "summarise", "tl;dr", "give a summary"),
    ),
    # -- data (spec 26) --
    ToolCatalogEntry(
        name="json_query",
        description="Extract a field, slice, or projection from JSON with a JMESPath query.",
        category="data",
        keywords=("query the json", "parse the json", "extract from the json", "jmespath"),
    ),
    # -- finance (spec 26) --
    ToolCatalogEntry(
        name="currency_convert",
        description="Convert an amount between currencies at current reference rates.",
        category="finance",
        keywords=("convert currency", "exchange rate", "convert that to", "in dollars", "in euros"),
    ),
    # -- media (spec 15) --
    ToolCatalogEntry(
        name="generate_image",
        description="Generate an image from a text prompt.",
        category="media",
        runtime_wired=True,
        keywords=("generate an image", "create an image", "draw a", "make a picture"),
    ),
    # -- media (spec 28) --
    ToolCatalogEntry(
        name="render_diagram",
        description="Render a Mermaid or Graphviz DOT diagram to a scalable SVG.",
        category="media",
        runtime_wired=True,
        keywords=(
            "draw a diagram",
            "render a diagram",
            "make a flowchart",
            "create a chart of",
            "draw a graph",
        ),
    ),
)

_BY_NAME: dict[str, ToolCatalogEntry] = {entry.name: entry for entry in TOOL_CATALOG}


def known_tool_names() -> frozenset[str]:
    """The set of all catalog tool names (the recommender's valid vocabulary)."""
    return frozenset(_BY_NAME)


def catalog_entry(name: str) -> ToolCatalogEntry | None:
    """Return the catalog entry for ``name``, or ``None`` if unknown."""
    return _BY_NAME.get(name)


def warn_unknown_declared_tools(
    declared: Iterable[str],
    *,
    logger: Logger | None = None,
) -> tuple[str, ...]:
    """Soft-WARN (never raise) for declared tools not in the catalog.

    MCP-style names (``mcp:...``) are skipped — they are resolved dynamically
    from configured MCP servers, not from this static catalog. Per
    D-26-X-known-tool-catalog there is NO hard validation: an unknown name is
    logged at ``WARNING`` and returned, but the persona still loads.

    Args:
        declared: The persona's ``tools`` allow-list.
        logger: Optional logger override (defaults to the module logger).

    Returns:
        The tuple of unknown tool names (excluding ``mcp:`` bindings), in input
        order.
    """
    log = logger if logger is not None else _logger
    known = known_tool_names()
    unknown: list[str] = []
    for name in declared:
        if name.startswith("mcp:"):
            continue
        if name not in known:
            unknown.append(name)
    if unknown:
        log.warning(
            "persona declares tools not in the known-tool catalog "
            "(no enforcement; check for typos): {unknown}",
            unknown=unknown,
        )
    return tuple(unknown)
