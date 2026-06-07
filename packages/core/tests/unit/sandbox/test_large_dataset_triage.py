"""Spec 17 T10 — Large-dataset triage rule (D-17-4) regression guard.

§6 criterion #4 (corner — sampling/refusal is "correct compute"): the
``data_analysis`` SKILL.md teaches the persona to gate on
``df.memory_usage(deep=True).sum() / 1024**2`` and pick one of three
actions per D-17-4:

  - resident < 100 MB        → full analysis
  - 100 MB ≤ resident < 500  → sample to 100,000 rows + banner
  - resident ≥ 500 MB        → refuse + explain

The numbers are calibrated against the Spec 12 sandbox
``ResourceLimits.memory_mb = 512`` floor. T10's job is to:

1. **Pin the bucket boundaries.** The triage function the SKILL.md
   teaches IS the test surface. If a future polish-pass shifts the
   numbers (e.g., to 80/450 because the sandbox floor changed), this
   test trips loud surfacing the cross-spec dependency.
2. **Verify the measurement code path works on a real DataFrame** —
   when pandas is available (sandbox image / dev env with [sandbox]
   extra). This proves the SKILL.md's ``memory_usage(deep=True).sum()``
   call returns the right magnitude. We do NOT generate 600 MB of
   random data in the unit suite (too slow); a small representative
   frame is enough to prove the code path.
3. **Document the dtype-sensitivity caveat.** Object columns inflate
   deep-memory wildly vs float64. The triage rule is calibrated
   against mixed-dtype CSV-loaded DataFrames; pure-float64 frames hit
   the floor at higher row counts. The test pins the expected
   behaviour so future contributors know it's intentional.

**Why this is a unit test, not an integration test.** The triage is
pure-Python arithmetic over a measurement function. The measurement
itself is pandas; when pandas is absent (default dev env), the test
runs the arithmetic alone. When pandas is present, it additionally
exercises the measurement code path on a real (small) DataFrame.
"""

from __future__ import annotations

import pytest

# Bucket boundaries from D-17-4 — the SKILL.md teaches these literal
# values. They're pinned here so a future SKILL.md edit that shifts
# them without explicit coordination trips this test.
FULL_ANALYSIS_CEILING_MB = 100
SAMPLE_BANNER_CEILING_MB = 500
SAMPLE_TARGET_ROWS = 100_000


def _triage_action(resident_mb: float) -> str:
    """The triage rule the ``data_analysis`` SKILL.md teaches verbatim.

    Pure function over the measurement — keeps the test surface
    independent of whether pandas is available.
    """
    if resident_mb < FULL_ANALYSIS_CEILING_MB:
        return "full_analysis"
    if resident_mb < SAMPLE_BANNER_CEILING_MB:
        return "sample_and_banner"
    return "refuse"


class TestD174TriageRuleBoundaries:
    """Pin the bucket boundaries the SKILL.md teaches. If these shift,
    coordinate with the SKILL.md teaching + the Spec 12 ResourceLimits
    floor (the calibration anchor).
    """

    def test_under_100mb_runs_full_analysis(self) -> None:
        assert _triage_action(0.5) == "full_analysis"
        assert _triage_action(50.0) == "full_analysis"
        assert _triage_action(99.9) == "full_analysis"

    def test_100mb_to_500mb_samples_with_banner(self) -> None:
        assert _triage_action(100.0) == "sample_and_banner"
        assert _triage_action(250.0) == "sample_and_banner"
        assert _triage_action(499.9) == "sample_and_banner"

    def test_500mb_or_above_refuses(self) -> None:
        assert _triage_action(500.0) == "refuse"
        assert _triage_action(600.0) == "refuse"
        assert _triage_action(10_000.0) == "refuse"

    def test_boundary_at_100_is_sample_not_full(self) -> None:
        """The boundary IS at 100 inclusive — < 100 runs full; >= 100
        samples. This pin prevents off-by-epsilon drift."""
        assert _triage_action(99.999) == "full_analysis"
        assert _triage_action(100.0) == "sample_and_banner"

    def test_boundary_at_500_is_refuse_not_sample(self) -> None:
        """The boundary IS at 500 inclusive — < 500 samples; >= 500
        refuses."""
        assert _triage_action(499.999) == "sample_and_banner"
        assert _triage_action(500.0) == "refuse"

    def test_sample_target_is_100k_rows(self) -> None:
        """SKILL.md teaches ``df.sample(n=100_000, random_state=0)``.
        Pin the number so a future polish-pass surfaces the change."""
        assert SAMPLE_TARGET_ROWS == 100_000


class TestMeasurementCodePathOnRealDataFrame:
    """When pandas is available (sandbox image / [sandbox] extra in CI /
    dev env), verify ``df.memory_usage(deep=True).sum()`` returns the
    expected magnitude. Skipped otherwise — pandas is a sandbox-only
    dependency per D-17-X-charting-lib-choice + D-12-2.
    """

    def test_float64_dataframe_measures_in_expected_range(self) -> None:
        """A 1000-row × 10-float64 frame is ~80 KB resident. The SKILL.md's
        measurement call returns a number in that range."""
        pd = pytest.importorskip("pandas")
        np = pytest.importorskip("numpy")
        df = pd.DataFrame(np.random.rand(1000, 10))
        resident_bytes = df.memory_usage(deep=True).sum()
        resident_mb = resident_bytes / 1024**2
        # 1000 rows × 10 cols × 8 bytes float64 ≈ 80 KB ≈ 0.08 MB
        # Plus the Index column (~8 KB). Tolerance bounds catch
        # accidental dtype regression (e.g. float32 → 0.04 MB).
        assert 0.05 < resident_mb < 0.20, f"unexpected resident_mb={resident_mb}"
        # Triage on this size: full analysis.
        assert _triage_action(resident_mb) == "full_analysis"

    def test_object_dtype_inflates_deep_memory_vs_float64(self) -> None:
        """The dtype-sensitivity caveat the SKILL.md + decisions.md
        document: object columns are ~10-50x heavier per cell than
        float64 because deep-memory measures actual string storage.

        This pin documents the calibration assumption (mixed-dtype
        CSV-loaded DataFrames are the target). A future test that
        constructs a pure-float64 600 MB frame would NOT trigger the
        same triage tier as a mixed-dtype 600 MB frame would have in
        practice — but BOTH would trigger refusal at our 500 MB
        ceiling. The rule holds; the calibration is honest.
        """
        pd = pytest.importorskip("pandas")
        np = pytest.importorskip("numpy")
        # 100 rows of float64
        df_float = pd.DataFrame(np.random.rand(100, 5))
        float_bytes = df_float.memory_usage(deep=True).sum()
        # 100 rows of strings, same shape
        df_obj = pd.DataFrame(
            {f"col{i}": [f"row{j}_string_{i}" for j in range(100)] for i in range(5)}
        )
        obj_bytes = df_obj.memory_usage(deep=True).sum()
        # Object columns measurably heavier than float64.
        assert obj_bytes > float_bytes, (
            f"expected object > float64; got obj={obj_bytes}, float={float_bytes}"
        )
        # The ratio is large (typically 5-20x for short strings).
        ratio = obj_bytes / float_bytes
        assert ratio > 2.0, (
            f"object-dtype inflation factor {ratio:.1f}x is unexpectedly small; "
            "the dtype-sensitivity caveat assumption may be wrong"
        )


class TestSkillMdTeachesTriageRule:
    """The SKILL.md prose teaches the exact triage numbers. This pin
    catches accidental drift between the test rule above and the
    teaching the persona sees.
    """

    def test_skill_md_teaches_100mb_boundary(self) -> None:
        from pathlib import Path

        skill_md = (
            Path(__file__).resolve().parents[3]
            / "src"
            / "persona"
            / "skills"
            / "builtin"
            / "data_analysis"
            / "SKILL.md"
        )
        content = skill_md.read_text(encoding="utf-8")
        # The SKILL.md teaches "< 100 MB → full" and "100 MB ≤ size < 500 MB → sample"
        # and ">= 500 MB → refuse". Pin the literal numbers + the action labels.
        assert "100 MB" in content
        assert "500 MB" in content
        assert "100,000 rows" in content
        # The measurement call the persona is told to use.
        assert "df.memory_usage(deep=True).sum()" in content
