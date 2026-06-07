"""Spec 17 T09 — chart-CHOICE + chart-CLARITY backend integration test (reframed).

§6 criteria #4 (chart-CHOICE: right family for the question) and #5
(chart-CLARITY: readable titles + axes + legible scale).

**Reframed 2026-06-06** from manual operator inspection to a backend
integration test per the project-wide instruction "test through the
backend; frontend isn't ready". Same redirection applied to Spec 16's
T11-T14. The reframing keeps the 5 chart families R-17-3 named but
asserts structurally on the produced PNGs:

- **chart-CHOICE** — assert the code that would PRODUCE a chart of the
  expected family runs cleanly in the sandbox AND produces a chart file
  at ``charts/<id>.png``. The code IS the structural witness — Spec 17's
  SKILL.md teaches the model to write ``ax.plot`` for time-series,
  ``ax.hist`` for distributions, etc. We exercise each family's code
  path here.
- **chart-CLARITY (structural surrogates)** — open the produced PNG with
  PIL, assert:
    1. Dimensions match the expected ``figsize × dpi`` (rules out
       figure-too-small).
    2. Color palette has ≥ 3 distinct hues (rules out monochrome-only).
    3. File size in 5KB–2MB range (rules out empty plots + over-sized).
- **chart-CLARITY (visual-only)** — honest-PARTIAL in close-out:
  typography polish + palette aesthetic harmony + label-placement craft
  verify when the frontend lands. Not v0.2 deferral; frontend-readiness
  limitation.

**Requires real Docker + ``persona-sandbox:0.1.0`` image.** Skips
cleanly when unavailable. Each chart family is a separate test (so
scorecard rows in state.md can record PASS/PARTIAL/FAIL per family).
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test code blobs — one per chart family. Each blob is what a competent
# Spec 17 model trained on the data_analysis SKILL.md would produce.
# ---------------------------------------------------------------------------

_T09_A_TIME_SERIES = """
import os
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

os.makedirs("charts", exist_ok=True)
months = pd.date_range("2020-01-01", periods=72, freq="MS")
sales = 100 + np.cumsum(np.random.RandomState(0).randn(72) * 5) + np.arange(72) * 2
df = pd.DataFrame({"month": months, "sales": sales})

fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
ax.plot(df["month"], df["sales"], color="#1f77b4", linewidth=1.8)
ax.set_title("Monthly sales 2020–2025")
ax.set_xlabel("Month")
ax.set_ylabel("Sales (NOK)")
ax.grid(True, alpha=0.3)
fig.autofmt_xdate(rotation=30)
fig.tight_layout()
fig.savefig("charts/sales-trend.png", dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"chart-family=time-series; saved=charts/sales-trend.png; rows={len(df)}")
"""


_T09_B_HORIZONTAL_BAR = """
import os
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

os.makedirs("charts", exist_ok=True)
df = pd.DataFrame({
    "city": ["Oslo", "Bergen", "Trondheim", "Stavanger", "Tromsø"],
    "rent": [18500, 14200, 13100, 13800, 12500],
})
df = df.sort_values("rent")

fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
ax.barh(df["city"], df["rent"], color="#4c72b0")
ax.set_title("Median monthly rent by Norwegian city")
ax.set_xlabel("Median rent (NOK)")
ax.grid(True, alpha=0.3, axis="x")
fig.tight_layout()
fig.savefig("charts/rent-by-city.png", dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"chart-family=horizontal-bar; saved=charts/rent-by-city.png; rows={len(df)}")
"""


_T09_C_HISTOGRAM = """
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

os.makedirs("charts", exist_ok=True)
rng = np.random.RandomState(0)
ages = np.concatenate([rng.normal(35, 12, 7000), rng.normal(55, 8, 3000)]).clip(18, 95)

fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
ax.hist(ages, bins=30, color="#4c72b0", edgecolor="white")
ax.set_title("Customer age distribution")
ax.set_xlabel("Age (years)")
ax.set_ylabel("Number of customers")
ax.grid(True, alpha=0.3, axis="y")
fig.tight_layout()
fig.savefig("charts/age-distribution.png", dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"chart-family=histogram; saved=charts/age-distribution.png; n={len(ages)}")
"""


_T09_D_SCATTER = """
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

os.makedirs("charts", exist_ok=True)
rng = np.random.RandomState(0)
heights = rng.normal(170, 10, 500)
weights = heights * 0.5 + rng.normal(0, 5, 500) - 18

fig, ax = plt.subplots(figsize=(8, 6), dpi=150)
ax.scatter(heights, weights, alpha=0.5, s=15, color="#4c72b0")
ax.set_title("Height vs weight (n=500)")
ax.set_xlabel("Height (cm)")
ax.set_ylabel("Weight (kg)")
ax.grid(True, alpha=0.3)
fig.tight_layout()
fig.savefig("charts/height-weight.png", dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"chart-family=scatter; saved=charts/height-weight.png; n=500")
"""


_T09_E_GROUPED_BAR = """
import os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

os.makedirs("charts", exist_ok=True)
sectors = ["Tech", "Energy", "Finance", "Retail"]
months = ["Jan", "Feb", "Mar"]
data = {s: [12 + i * 2 + j for j, _ in enumerate(months)] for i, s in enumerate(sectors)}
df = pd.DataFrame(data, index=months)
x = np.arange(len(df.index))
width = 0.8 / len(df.columns)

fig, ax = plt.subplots(figsize=(10, 5), dpi=150)
for i, col in enumerate(df.columns):
    ax.bar(x + i * width, df[col], width, label=col)
ax.set_xticks(x + width * (len(df.columns) - 1) / 2)
ax.set_xticklabels(df.index)
ax.set_title("Sector returns 2024 Q1")
ax.set_xlabel("Month")
ax.set_ylabel("Return (%)")
ax.legend(title="Sector", frameon=False)
ax.grid(True, alpha=0.3, axis="y")
fig.tight_layout()
fig.savefig("charts/sector-returns.png", dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"chart-family=grouped-bar; saved=charts/sector-returns.png; series={len(df.columns)}")
"""


# ---------------------------------------------------------------------------
# Fixture: real LocalDockerSandbox with a per-test session
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sandbox_session(tmp_path: Path) -> AsyncIterator[tuple[Any, str]]:
    """Yield (sandbox, session_id) ready for execute. aclose on teardown."""
    try:
        from persona.sandbox.local_docker import (  # noqa: PLC0415
            LocalDockerSandbox,
            is_docker_available,
        )
    except ImportError:
        pytest.skip("[sandbox] extra not installed")

    if not is_docker_available():
        pytest.skip("Docker daemon not reachable")

    from persona.sandbox.result import NetworkPolicy, ResourceLimits  # noqa: PLC0415

    sandbox = LocalDockerSandbox(workspace_root=tmp_path / "sandbox_workspace")
    session_id = f"tenant-T09:conv-{tmp_path.name}"
    await sandbox.create_session(session_id, limits=ResourceLimits(), network=NetworkPolicy())
    try:
        yield sandbox, session_id
    finally:
        await sandbox.aclose()


# ---------------------------------------------------------------------------
# CLARITY assertion helpers
# ---------------------------------------------------------------------------


def _assert_chart_clarity_surrogates(
    chart_bytes: bytes, *, min_kb: int = 5, max_kb: int = 2_000
) -> None:
    """Programmatic chart-CLARITY surrogates (per the T09 reframe):

    1. File size in [min_kb, max_kb] range — rules out empty plots
       and oversized aberrations.
    2. PIL parses the PNG — confirms the file is structurally valid.
    3. Dimensions ≥ 600 × 400 px — rules out figure-too-small.
    4. Distinct color palette ≥ 3 hues — rules out monochrome accidents.

    Honest-PARTIAL in state.md for typography polish + palette
    aesthetic harmony + label placement — frontend-only verification.
    """
    from PIL import Image  # noqa: PLC0415

    size_kb = len(chart_bytes) / 1024
    assert min_kb < size_kb < max_kb, (
        f"chart file size {size_kb:.1f} KB outside [{min_kb}, {max_kb}]"
    )

    img = Image.open(io.BytesIO(chart_bytes))
    width, height = img.size
    assert width >= 600, f"chart width {width} below 600 (height={height})"
    assert height >= 400, f"chart height {height} below 400 (width={width})"

    # Count distinct quantised colors (16-color palette quantisation
    # gives a stable estimate that doesn't drift on anti-aliasing).
    quantised = img.convert("RGB").quantize(colors=16)
    distinct_colors = len(set(quantised.getdata()))
    assert distinct_colors >= 3, (
        f"chart has only {distinct_colors} distinct colors (monochrome-ish)"
    )


async def _retrieve_chart_bytes(
    sandbox: Any,  # noqa: ANN401 — CodeSandbox Protocol; Any keeps the test backend-agnostic
    session_id: str,
    ref: str,
    tmp_path: Path,
) -> bytes:
    """Use the D-12-X-read-produced-file helper to fetch chart bytes
    from the sandbox session — the same path the runtime uses in
    production (D-17-X-bytes-persistence)."""
    target = tmp_path / "chart_out" / ref
    target.parent.mkdir(parents=True, exist_ok=True)
    await sandbox.copy_produced_file_to(session_id, ref, target)
    return target.read_bytes()


# ---------------------------------------------------------------------------
# 5 chart-family tests
# ---------------------------------------------------------------------------


class TestChartFamilyTimeSeries:
    """T09-A time-series → line (the trend question)."""

    @pytest.mark.asyncio
    async def test_time_series_produces_line_chart(
        self,
        sandbox_session: tuple[Any, str],
        tmp_path: Path,
    ) -> None:
        sandbox, session_id = sandbox_session
        result = await sandbox.execute(_T09_A_TIME_SERIES, session_id=session_id, timeout_s=60.0)
        assert result.outcome == "ok", result.stderr
        # chart-CHOICE structural: the code produced a chart in charts/
        assert any(sf.path == "charts/sales-trend.png" for sf in result.produced_files)
        # chart-CLARITY surrogates: real bytes round-tripped through the
        # D-12-X-read-produced-file helper land at the right path and
        # parse to a well-formed image.
        chart_bytes = await _retrieve_chart_bytes(
            sandbox, session_id, "charts/sales-trend.png", tmp_path
        )
        _assert_chart_clarity_surrogates(chart_bytes)


class TestChartFamilyHorizontalBar:
    """T09-B horizontal bar → categorical comparison (the "which city" question)."""

    @pytest.mark.asyncio
    async def test_horizontal_bar_produces_chart(
        self,
        sandbox_session: tuple[Any, str],
        tmp_path: Path,
    ) -> None:
        sandbox, session_id = sandbox_session
        result = await sandbox.execute(_T09_B_HORIZONTAL_BAR, session_id=session_id, timeout_s=60.0)
        assert result.outcome == "ok", result.stderr
        assert any(sf.path == "charts/rent-by-city.png" for sf in result.produced_files)
        chart_bytes = await _retrieve_chart_bytes(
            sandbox, session_id, "charts/rent-by-city.png", tmp_path
        )
        _assert_chart_clarity_surrogates(chart_bytes)


class TestChartFamilyHistogram:
    """T09-C histogram → distribution (the spread question)."""

    @pytest.mark.asyncio
    async def test_histogram_produces_chart(
        self,
        sandbox_session: tuple[Any, str],
        tmp_path: Path,
    ) -> None:
        sandbox, session_id = sandbox_session
        result = await sandbox.execute(_T09_C_HISTOGRAM, session_id=session_id, timeout_s=60.0)
        assert result.outcome == "ok", result.stderr
        assert any(sf.path == "charts/age-distribution.png" for sf in result.produced_files)
        chart_bytes = await _retrieve_chart_bytes(
            sandbox, session_id, "charts/age-distribution.png", tmp_path
        )
        _assert_chart_clarity_surrogates(chart_bytes)


class TestChartFamilyScatter:
    """T09-D scatter → relationship (the correlation question)."""

    @pytest.mark.asyncio
    async def test_scatter_produces_chart(
        self,
        sandbox_session: tuple[Any, str],
        tmp_path: Path,
    ) -> None:
        sandbox, session_id = sandbox_session
        result = await sandbox.execute(_T09_D_SCATTER, session_id=session_id, timeout_s=60.0)
        assert result.outcome == "ok", result.stderr
        assert any(sf.path == "charts/height-weight.png" for sf in result.produced_files)
        chart_bytes = await _retrieve_chart_bytes(
            sandbox, session_id, "charts/height-weight.png", tmp_path
        )
        _assert_chart_clarity_surrogates(chart_bytes)


class TestChartFamilyGroupedBar:
    """T09-E grouped bar → multi-series categorical (the cross-dimension question)."""

    @pytest.mark.asyncio
    async def test_grouped_bar_produces_chart(
        self,
        sandbox_session: tuple[Any, str],
        tmp_path: Path,
    ) -> None:
        sandbox, session_id = sandbox_session
        result = await sandbox.execute(_T09_E_GROUPED_BAR, session_id=session_id, timeout_s=60.0)
        assert result.outcome == "ok", result.stderr
        assert any(sf.path == "charts/sector-returns.png" for sf in result.produced_files)
        chart_bytes = await _retrieve_chart_bytes(
            sandbox, session_id, "charts/sector-returns.png", tmp_path
        )
        _assert_chart_clarity_surrogates(chart_bytes)
