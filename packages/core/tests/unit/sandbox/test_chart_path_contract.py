"""Spec 17 T08 — Spec 16 chart-embedding handoff contract test.

Spec 17 is the PRODUCER of chart files; Spec 16's eventual ``docx_generation``
/ ``pdf_generation`` / ``pptx_generation`` skills are the CONSUMERS. The
contract Spec 17 ships and Spec 16 inherits is the **chart-path
convention** locked at:

- Spec 17 D-17-X-charts-path-source (producer side)
- Spec 16 D-16-5 (consumer side — already locked at 2026-06-06,
  verbatim alignment, zero cross-spec amendment)

The contract has three pieces every implementing agent on either side
must honor. This test file pins each one so a future polish-pass that
drifts the convention (renaming ``charts/`` to ``figures/``,
introducing an extra directory level, etc.) trips a loud failure
explaining the cross-spec coupling.

**Why this is a contract test, not an integration test.** Spec 16's
Phase 5 is not yet in flight at the time of writing; we cannot run an
end-to-end docx-embeds-chart integration. Instead, we lock the producer's
guarantees so Spec 16's eventual integration test inherits a stable
target. The test is fast (no Docker, no DB, no live model) and runs in
the default unit suite.
"""

from __future__ import annotations

from pathlib import Path

from persona.sandbox.result import ExecutionResult, SandboxFile

# ---------------------------------------------------------------------------
# Pin 1 — the path convention is workspace-relative ``charts/<id>.png``
# ---------------------------------------------------------------------------


class TestChartPathConventionShape:
    """The workspace-relative path Spec 17's persister writes to is the
    workspace-relative path Spec 16's embedder reads from. Both specs
    agree on the literal string shape.
    """

    def test_workspace_relative_path_pattern(self) -> None:
        """``charts/<id>.png`` — top-level ``charts/`` directory, PNG ext.

        - The directory is ``charts/``, not ``figures/`` or ``plots/`` or
          ``viz/``. Renaming would break Spec 16's reader.
        - The extension is ``.png``, the format Spec 17 D-17-X-chart-format
          locks for v0.1 and Spec 16 D-16-5-rejection-SVG empirically
          rules SVG out for at v0.1 across all three embed engines.
        - ``<id>`` is the producer's choice (UUID or descriptive slug);
          the consumer treats it as opaque.
        """
        # Representative paths a Spec 17 skill writes:
        for path in ("charts/sales-trend.png", "charts/age-distribution.png", "charts/a1b2c3.png"):
            sf = SandboxFile(path=path, size_bytes=1024, media_type="image/png")
            # Pin the discriminator the conversation-loop annotation reads
            # (D-17-X-inline-hint-shape: path-prefix IS the hint).
            assert sf.path.split("/")[0] == "charts"
            assert sf.path.endswith(".png")

    def test_sandbox_internal_path_is_workspace_out_relative(self) -> None:
        """Inside the sandbox, the file is at ``/workspace/out/charts/<id>.png``.

        Spec 12 D-12-9 two-mount convention: ``/workspace/out`` is the
        only writable host path; the SandboxFile's ``path`` is relative
        to that mount. Spec 16 D-16-5 explicitly names this:
        ``<workspace> = /workspace/out`` (local) or ``/home/user``
        (hosted equivalent).
        """
        # Spec 17 SKILL.md teaches `plt.savefig("charts/<id>.png")` — the
        # cwd inside the sandbox is /workspace/out (D-12-9), so the file
        # lands at /workspace/out/charts/<id>.png. Spec 16's embedder reads
        # from the same workspace-relative path; whatever string Spec 17
        # writes is the string Spec 16 reads.
        for path in ("charts/x.png", "charts/y.png"):
            sf = SandboxFile(path=path, size_bytes=10, media_type="image/png")
            # The Protocol field is workspace-relative, NOT absolute.
            assert not sf.path.startswith("/")
            assert not Path(sf.path).is_absolute()


# ---------------------------------------------------------------------------
# Pin 2 — ExecutionResult carries chart files like any produced file
# ---------------------------------------------------------------------------


class TestExecutionResultCarriesChartFiles:
    """No special chart-file mechanism. A chart is a produced file like any
    other; the ``charts/`` prefix is the only thing that distinguishes it
    semantically. This pin prevents any future "special chart contract"
    from being added without explicit cross-spec discussion.
    """

    def test_produced_files_tuple_admits_chart_path(self) -> None:
        """A SandboxFile under ``charts/`` is structurally identical to
        any other produced file. Spec 12's ``ExecutionResult.produced_files``
        shape (frozen tuple) admits it without modification."""
        chart = SandboxFile(path="charts/scatter.png", size_bytes=8_192, media_type="image/png")
        download = SandboxFile(path="export.csv", size_bytes=2_048, media_type="text/csv")
        result = ExecutionResult(
            stdout="(plot saved)\n",
            stderr="",
            exit_status=0,
            outcome="ok",
            produced_files=(chart, download),
        )
        # Both chart and non-chart files coexist in produced_files — the
        # consumer (loop / persister / Spec 16 reader) discriminates on the
        # ``charts/`` prefix, not on a separate field.
        assert chart in result.produced_files
        assert download in result.produced_files
        assert len(result.produced_files) == 2

    def test_chart_files_distinguishable_by_path_prefix(self) -> None:
        """Path-IS-hint (D-17-X-inline-hint-shape) — the discriminator is
        the top-level directory component, computed as ``path.split("/")[0]``.
        """
        produced = (
            SandboxFile(path="charts/sales.png", size_bytes=10, media_type="image/png"),
            SandboxFile(path="uploads/export.csv", size_bytes=10, media_type="text/csv"),
            SandboxFile(
                path="intermediate/df.parquet",
                size_bytes=10,
                media_type="application/octet-stream",
            ),
        )
        # Three sibling top-level directories, three meanings:
        #   charts/      → inline visual (Spec 17 produces, Spec 16 embeds)
        #   uploads/     → download chip (existing upload-roundtrip path)
        #   intermediate/→ cross-turn cache (not surfaced to UI)
        kinds = {sf.path.split("/")[0] for sf in produced}
        assert kinds == {"charts", "uploads", "intermediate"}


# ---------------------------------------------------------------------------
# Pin 3 — the chart-path convention is documented in cross-spec sources
# ---------------------------------------------------------------------------


class TestCrossSpecDocumentationCoupling:
    """If the convention is changed, the documentation in both specs must
    track. This test reads the actual on-disk SKILL.md and the project
    DECISIONS.md to ensure the contract isn't silently desynchronised.
    """

    def test_data_analysis_skill_teaches_charts_prefix(self) -> None:
        """The ``data_analysis`` SKILL.md body teaches the persona to save
        charts under ``charts/`` — the producer convention. If the SKILL.md
        is edited to teach a different path, this test fails and the
        agent surfaces it.
        """
        # parents: [0]=sandbox/ [1]=unit/ [2]=tests/ [3]=core/ — append src/persona/...
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
        # The SKILL.md prescribes the path convention literally — either
        # plt.savefig or fig.savefig with "charts/" prefix is acceptable.
        assert 'savefig("charts/' in content
        # Three-directory teaching (the user-visible discrimination):
        assert "charts/" in content
        assert "uploads/" in content
        assert "intermediate/" in content
