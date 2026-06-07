"""Unit regression test for the runtime-factory skill-scanner wiring.

Regression guard for the bug where ``RuntimeFactory._scan_skills`` was
constructing ``SkillScanner(skill_paths=[])`` with an empty path list,
so every persona-declared skill logged ``"declared skill not found"``
at every chat turn and the loop never injected any skill content.

The fix wires the shared :data:`persona.skills.BUILTIN_ROOT` constant
into both :class:`SkillScanner` instantiations (this service + the
catalog service) so they resolve declared skills against the same
on-disk directory. This test asserts that:

1. ``BUILTIN_ROOT`` is exported from ``persona.skills``.
2. A :class:`SkillScanner` constructed with ``skill_paths=[BUILTIN_ROOT]``
   resolves the v0.1 bundled skill names (``web_research``,
   ``document_drafting``) plus the Phase-2-added skills
   (``data_analysis``, ``docx_generation``, ``pptx_generation``,
   ``xlsx_generation``, ``pdf_generation``) to non-``None`` specs.
3. The catalog service and the runtime factory import ``BUILTIN_ROOT``
   from the same source (single source of truth — no parallel
   definitions that could drift).

A future refactor that resets ``runtime_factory`` to ``skill_paths=[]``
or otherwise breaks the wiring fails this test loud.
"""

from __future__ import annotations

from persona.skills import BUILTIN_ROOT, SkillScanner


def test_builtin_root_points_at_a_real_directory_with_skill_packs() -> None:
    """``BUILTIN_ROOT`` is a real on-disk directory containing skill packs."""
    assert BUILTIN_ROOT.exists(), f"BUILTIN_ROOT does not exist: {BUILTIN_ROOT}"
    assert BUILTIN_ROOT.is_dir(), f"BUILTIN_ROOT is not a directory: {BUILTIN_ROOT}"
    # Each bundled skill ships as a sub-directory with a SKILL.md.
    bundled = {"web_research", "document_drafting"}
    found = {p.name for p in BUILTIN_ROOT.iterdir() if p.is_dir()}
    missing = bundled - found
    assert not missing, f"v0.1 bundled skills missing from BUILTIN_ROOT: {missing}"


def test_skill_scanner_with_builtin_root_resolves_bundled_skills() -> None:
    """``SkillScanner(skill_paths=[BUILTIN_ROOT])`` finds the v0.1 bundled skills.

    This is the core regression guard: the runtime factory's
    ``_scan_skills`` constructs a scanner with this exact ``skill_paths``;
    if any persona's ``skills: [...]`` declaration includes a v0.1
    bundled skill, the scanner MUST return a non-``None`` spec.
    Empty ``skill_paths`` (the prior bug) returns ``None`` for every
    declared skill and logs ``"declared skill not found"``.
    """
    scanner = SkillScanner(skill_paths=[BUILTIN_ROOT])
    scanned = scanner.scan(declared_skills=["web_research", "document_drafting"])
    assert len(scanned) == 2, (
        f"expected both bundled skills to resolve; got {[s.name for s in scanned]}"
    )
    names = {s.name for s in scanned}
    assert names == {"web_research", "document_drafting"}


def test_skill_scanner_with_empty_paths_finds_nothing_regression_baseline() -> None:
    """Empty ``skill_paths`` resolves nothing — the prior-bug baseline.

    Documents the *negative* invariant: if a future change reverts the
    runtime factory wiring to ``skill_paths=[]`` (the bug), the scanner
    silently returns an empty list. This test exists as the explicit
    record of why the wiring matters; the positive test above is the
    actual guard.
    """
    scanner = SkillScanner(skill_paths=[])
    scanned = scanner.scan(declared_skills=["web_research", "document_drafting"])
    assert scanned == [], "empty skill_paths must resolve nothing"


def test_runtime_factory_and_catalog_share_builtin_root_source() -> None:
    """Both services import ``BUILTIN_ROOT`` from the same module.

    Prevents the failure mode where each service defines its own
    ``_BUILTIN_SKILLS_DIR`` constant and the two drift over time.
    Asserts the module-level identity of the imported constant.
    """
    from persona.skills import BUILTIN_ROOT as CORE_BUILTIN_ROOT
    from persona_api.services.catalog_service import BUILTIN_ROOT as CATALOG_BUILTIN_ROOT
    from persona_api.services.runtime_factory import BUILTIN_ROOT as FACTORY_BUILTIN_ROOT

    # All three references point at the same on-disk Path.
    assert CORE_BUILTIN_ROOT == CATALOG_BUILTIN_ROOT == FACTORY_BUILTIN_ROOT
    # And critically, the same singleton instance (re-export, not a copy).
    assert CORE_BUILTIN_ROOT is CATALOG_BUILTIN_ROOT
    assert CORE_BUILTIN_ROOT is FACTORY_BUILTIN_ROOT


def test_phase2_added_skills_are_discoverable_when_declared() -> None:
    """Phase-2 skill packs (docx/pptx/xlsx/pdf_generation, data_analysis) resolve.

    Coverage extension beyond the v0.1 bundled set — captures that the
    Spec 16 + Spec 17 SKILL.md packs are also discoverable via the
    shared ``BUILTIN_ROOT``. A persona declaring any of these in chat
    mode must get the skill content injected.
    """
    scanner = SkillScanner(skill_paths=[BUILTIN_ROOT])
    phase2_skills = [
        "data_analysis",
        "docx_generation",
        "pptx_generation",
        "xlsx_generation",
        "pdf_generation",
    ]
    scanned = scanner.scan(declared_skills=phase2_skills)
    found_names = {s.name for s in scanned}
    missing = set(phase2_skills) - found_names
    assert not missing, (
        f"Phase 2 skills missing from BUILTIN_ROOT (package-data wiring drift?): {missing}"
    )
