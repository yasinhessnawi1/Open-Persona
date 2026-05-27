"""``persona run`` — stub until spec 06 ships the agentic loop."""
# ruff: noqa: B008 — typer.Argument/Option in defaults is the framework idiom

from __future__ import annotations

import typer

__all__ = ["run"]


def run(
    persona_path: str = typer.Argument(..., help="Path to a persona YAML."),
    task: str = typer.Argument(..., help="Task description for the persona to execute."),
) -> None:
    """Stub. The agentic loop arrives in spec 06."""
    _ = persona_path, task
    typer.echo(
        "Agentic runs require the persona-runtime package. "
        "Install it with `pip install persona-runtime`. "
        "(stub until spec 06 ships)",
        err=True,
    )
    raise typer.Exit(code=2)
