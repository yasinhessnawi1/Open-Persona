"""``persona init`` — interactive guided creation of a v1.0 persona YAML.

Ordered prompts per D-01-3: name → role → background → constraints → tools
→ skills → save. Empty input on optional sections means "no entries."
"""
# ruff: noqa: B008 — typer.Argument/Option in defaults is the framework idiom

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer
import yaml

from persona.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterator

_log = get_logger("cli")

__all__ = ["init"]


# Reasonable defaults for the tool/skill catalogues. Spec 03 and spec 04
# replace these with real registry lookups.
_KNOWN_TOOLS: tuple[str, ...] = ("web_search", "web_fetch", "file_read", "file_write")
_KNOWN_SKILLS: tuple[str, ...] = ("web_research", "document_drafting")


def init(
    output: Path = typer.Option(
        Path("persona.yaml"),
        "--output",
        "-o",
        help="Where to write the persona YAML.",
    ),
    description: str | None = typer.Option(
        None,
        "--from",
        help="LLM-assisted creation (deferred to spec 10). Prints a stub message.",
    ),
) -> None:
    """Interactively build a v1.0 persona YAML.

    ``--from "<description>"`` is reserved for spec 10's authoring flow.
    For v0.1 it prints a stub message and exits.
    """
    if description is not None:
        typer.echo(
            "LLM-assisted authoring (persona init --from) requires the hosted "
            "API or a configured model backend. Run `persona init` for the "
            "interactive flow.",
            err=True,
        )
        raise typer.Exit(code=2)

    if output.exists() and not typer.confirm(f"{output} already exists. Overwrite?", default=False):
        typer.echo("aborted; no changes written")
        raise typer.Exit(code=0)

    typer.echo("Building a new persona. Press Enter on optional fields to skip.\n")
    persona_id = typer.prompt("persona_id", default=output.stem)
    name = typer.prompt("identity.name")
    role = typer.prompt("identity.role")
    background = typer.prompt("identity.background")
    language_default = typer.prompt("identity.language_default", default="en")
    constraints = list(_prompt_list("constraints"))
    tools = _prompt_choices("tools", _KNOWN_TOOLS)
    skills = _prompt_choices("skills", _KNOWN_SKILLS)

    doc: dict[str, object] = {
        "persona_id": persona_id,
        "schema_version": "1.0",
        "identity": {
            "name": name,
            "role": role,
            "background": background,
            "language_default": language_default,
        },
    }
    if constraints:
        doc["identity"]["constraints"] = constraints  # type: ignore[index]
    if tools:
        doc["tools"] = tools
    if skills:
        doc["skills"] = skills

    output.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    typer.echo(f"\nWrote {output}")
    typer.echo(f"Validate with: persona validate {output}")


def _prompt_list(label: str) -> Iterator[str]:
    """Repeatedly prompt until the user enters a blank line."""
    typer.echo(f"\nEnter {label}, one per line. Empty line to finish.")
    while True:
        value = typer.prompt(f"  {label}[+]", default="", show_default=False)
        if not value.strip():
            break
        yield value.strip()


def _prompt_choices(label: str, choices: tuple[str, ...]) -> list[str]:
    typer.echo(f"\nAvailable {label}: {', '.join(choices)}")
    raw = typer.prompt(
        f"{label} (comma-separated, empty to skip)",
        default="",
        show_default=False,
    )
    if not raw.strip():
        return []
    selected = [s.strip() for s in raw.split(",") if s.strip()]
    invalid = [s for s in selected if s not in choices]
    if invalid:
        typer.echo(
            f"warning: unknown {label} entries kept as-is: {', '.join(invalid)}",
            err=True,
        )
    return selected
