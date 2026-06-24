"""Structural guard: the OWNED SURFACE stays import-decoupled from persona_api.

The reversibility guarantee (C1-D-1, refined at T3): the connector's owned
surface — everything under :mod:`persona_connectors.domain` (the ``Connector``
protocol, the normalisation contracts, the parallel-conversation model,
name-parsing, the linking ports + pure lifecycle) — must NOT import
``persona_api``. With that invariant held, a future extract-to-core moves
``domain/`` wholesale (a dependency swap, not a reshape).

API-coupling is permitted only in an explicit allowlist: the composition root
(``composition``), a future service entry point (``__main__``), and the
``infra`` adapters (the concrete ports — the C0 "Protocol in owned surface,
adapter in infra" pattern). Two assertions enforce this: (1) ``domain/`` is
api-free; (2) any module that imports persona_api is in the allowlist.

This test parses each module's AST; it strengthens automatically as later tasks
add modules.
"""

from __future__ import annotations

import ast
import pathlib

import persona_connectors

_PKG_DIR = pathlib.Path(persona_connectors.__file__).parent


def _imports_persona_api(source: str) -> bool:
    """True if the module's AST has any ``import persona_api`` / ``from persona_api``."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                a.name == "persona_api" or a.name.startswith("persona_api.") for a in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "persona_api" or mod.startswith("persona_api."):
                return True
    return False


def _module_stem(path: pathlib.Path) -> str:
    """The dotted module stem relative to the package, for allowlist matching."""
    rel = path.relative_to(_PKG_DIR).with_suffix("")
    return ".".join(rel.parts)


def _is_allowlisted(stem: str) -> bool:
    """API-coupling is permitted only in the composition root, a service entry
    point, or the infra adapters."""
    return stem in {"composition", "__main__"} or stem == "infra" or stem.startswith("infra.")


def test_domain_owned_surface_is_api_free() -> None:
    """The reversibility invariant: no module under domain/ imports persona_api."""
    offenders: list[str] = []
    for py_file in (_PKG_DIR / "domain").rglob("*.py"):
        if _imports_persona_api(py_file.read_text(encoding="utf-8")):
            offenders.append(_module_stem(py_file))
    assert offenders == [], (
        f"these domain/ modules import persona_api, breaking the reversibility "
        f"guarantee (C1-D-1): {offenders}"
    )


def test_api_coupling_is_confined_to_the_allowlist() -> None:
    """Every persona_api-importing module is in the allowlist (composition/__main__/infra)."""
    offenders: list[str] = []
    for py_file in _PKG_DIR.rglob("*.py"):
        stem = _module_stem(py_file)
        if _is_allowlisted(stem):
            continue
        if _imports_persona_api(py_file.read_text(encoding="utf-8")):
            offenders.append(stem)
    assert offenders == [], (
        f"these modules import persona_api but are not allow-listed "
        f"(composition / __main__ / infra) — C1-D-1: {offenders}"
    )


def test_the_composition_root_is_actually_api_coupled() -> None:
    """Sanity: composition.py genuinely imports persona_api (the guard isn't vacuous)."""
    source = (_PKG_DIR / "composition.py").read_text(encoding="utf-8")
    assert _imports_persona_api(source)
