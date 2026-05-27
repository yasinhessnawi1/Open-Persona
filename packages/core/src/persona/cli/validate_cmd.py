"""``persona validate <path>`` — schema check on a YAML."""
# ruff: noqa: B008 — typer.Argument/Option in defaults is the framework idiom

from __future__ import annotations

from pathlib import Path  # noqa: TC003 — typer needs runtime access

import typer
from pydantic import ValidationError

from persona.errors import PersonaError, SchemaVersionMismatchError
from persona.schema.persona import Persona

__all__ = ["validate"]


def validate(path: Path = typer.Argument(..., help="Path to the persona YAML file.")) -> None:
    """Validate ``path`` against the v1.0 persona schema."""
    try:
        Persona.from_yaml(path)
    except SchemaVersionMismatchError as exc:
        typer.echo(f"schema version mismatch: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    except ValidationError as exc:
        typer.echo("invalid persona YAML:", err=True)
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", ()))
            typer.echo(f"  {loc}: {err.get('msg', '')}", err=True)
        raise typer.Exit(code=1) from exc
    except PersonaError as exc:
        typer.echo(f"{exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"valid: {path}")
