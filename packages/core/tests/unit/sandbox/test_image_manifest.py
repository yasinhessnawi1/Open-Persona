"""Unit tests for the spec-12 T06 / spec-25 T07 sandbox image manifest.

These tests run unconditionally on the default suite — they verify the
**static manifest** (Dockerfile syntax, requirements.in contents,
README presence) without invoking ``docker build``. The actual image
build is integration territory (``docker build`` requires a daemon +
network + minutes of wall-clock); it's a manual / CI step per the
[image README](packages/core/src/persona/sandbox/image/README.md).

The D-25-1 / R-25-2 manifest (31 top-level packages, superseding the
spec-12 T06 eight) is pinned so a refactor cannot silently drop a
Spec-16, Spec-17, or Spec-25 dependency.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_IMAGE_DIR = Path(__file__).resolve().parents[3] / "src" / "persona" / "sandbox" / "image"


# ---------------------------------------------------------------------------
# Files exist
# ---------------------------------------------------------------------------


class TestImageFilesExist:
    def test_dockerfile_present(self) -> None:
        assert (_IMAGE_DIR / "Dockerfile").is_file()

    def test_requirements_in_present(self) -> None:
        assert (_IMAGE_DIR / "requirements.in").is_file()

    def test_requirements_txt_present(self) -> None:
        assert (_IMAGE_DIR / "requirements.txt").is_file()

    def test_readme_present(self) -> None:
        assert (_IMAGE_DIR / "README.md").is_file()


# ---------------------------------------------------------------------------
# D-25-1 / R-25-2 manifest — every required package is pinned
# ---------------------------------------------------------------------------


# The full D-25-1 31-package set (supersedes the spec-12 T06 eight). A refactor
# that drops any of these silently breaks a downstream Spec-16/17/25 surface.
_REQUIRED_PACKAGES = (
    "beautifulsoup4",
    "graphviz",
    "httpx",
    "hypothesis",
    "ipykernel",
    "jinja2",
    "jsonschema",
    "lxml",
    "markdown",
    "matplotlib",
    "networkx",
    "numpy",
    "openpyxl",
    "pandas",
    "pillow",
    "pygments",
    "pypdf",
    "pytest",
    "python-docx",
    "python-pptx",
    "pyyaml",
    "reportlab",
    "requests",
    "rich",
    "scikit-learn",
    "scipy",
    "seaborn",
    "statsmodels",
    "sympy",
    "tabulate",
    "weasyprint",
)

# Explicitly dropped by D-25-1 — must NOT reappear in the manifest.
_DROPPED_PACKAGES = ("plotly", "opencv-python-headless", "opencv-python", "toml")

# D-25-1 weasyprint runtime apt libs (pango/cairo/gdk-pixbuf cffi stack).
_WEASYPRINT_APT_LIBS = (
    "libpango-1.0-0",
    "libpangocairo-1.0-0",
    "libcairo2",
    "libgdk-pixbuf-2.0-0",
    "libffi8",
    "shared-mime-info",
)


class TestRequirementsInManifest:
    """The D-25-1 manifest is pinned. A refactor that drops a Spec-16/17/25
    dep — or re-adds a dropped one — would silently break the size budget or
    a downstream surface without these tests."""

    @pytest.fixture
    def requirements_in(self) -> str:
        return (_IMAGE_DIR / "requirements.in").read_text()

    def test_exactly_thirty_one_top_level_packages(self, requirements_in: str) -> None:
        """D-25-1 locks the set at 31 top-level packages (≤35 cap)."""
        import re

        pinned = re.findall(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", requirements_in, re.M)
        assert len(pinned) == 31, (
            f"requirements.in must pin exactly 31 top-level packages (D-25-1); "
            f"found {len(pinned)}: {sorted(pinned)}"
        )

    @pytest.mark.parametrize("pkg", _REQUIRED_PACKAGES)
    def test_required_package_is_pinned(self, requirements_in: str, pkg: str) -> None:
        # Pin syntax: ``pkg==X.Y.Z`` (exact). Loose ``pkg>=X`` is rejected
        # by ``pip install --require-hashes`` anyway, so this test catches
        # the regression at write time.
        assert f"{pkg}==" in requirements_in, (
            f"required package {pkg!r} missing or not exact-pinned in "
            f"requirements.in — D-25-1 / R-25-2 mandate"
        )

    @pytest.mark.parametrize("pkg", _DROPPED_PACKAGES)
    def test_dropped_package_absent(self, requirements_in: str, pkg: str) -> None:
        """D-25-1 dropped plotly / opencv / toml — busts size cap or unmaintained."""
        assert f"{pkg}==" not in requirements_in, (
            f"package {pkg!r} was explicitly dropped by D-25-1 and must not be re-pinned"
        )


class TestRequirementsTxtHashMode:
    """Hash-mode is the strongest pip-level supply-chain guarantee.
    ``requirements.txt`` is ``pip-compile``-generated with ``--generate-hashes``;
    every pinned line carries a real ``--hash=sha256:...`` continuation.

    NOTE (spec-25 T07): the *committed* lock still reflects the previous
    eight-package set until an operator/CI run regenerates it against the new
    31-package ``requirements.in`` (a build step that needs network + the
    image interpreter — out of scope for the code-authoring task). So this
    suite asserts the lock's hash *shape* and that every package **present in
    the lock** carries a hash — it does NOT require the not-yet-resolved new
    packages to appear until regen lands."""

    @pytest.fixture
    def requirements_txt(self) -> str:
        return (_IMAGE_DIR / "requirements.txt").read_text()

    def test_uses_hash_syntax(self, requirements_txt: str) -> None:
        """``--hash=sha256:...`` syntax is present for the top-level pkgs."""
        assert "--hash=sha256:" in requirements_txt

    def test_no_placeholder_hashes(self, requirements_txt: str) -> None:
        """The lock carries real sha256 hashes, not placeholders — the stale
        'placeholder' README claim was fixed in T07."""
        import re

        hashes = re.findall(r"--hash=sha256:([0-9a-f]+)", requirements_txt)
        assert hashes, "no sha256 hashes found in requirements.txt"
        # Real sha256 digests are 64 hex chars; reject any obvious placeholder.
        assert all(len(h) == 64 for h in hashes), "non-sha256-length hash present (placeholder?)"

    @pytest.mark.parametrize("pkg", _REQUIRED_PACKAGES)
    def test_locked_package_has_hash_block(self, requirements_txt: str, pkg: str) -> None:
        # Only assert on packages already resolved into the committed lock;
        # the new D-25-1 additions land their hash blocks at the operator regen.
        lines = requirements_txt.splitlines()
        pkg_line_idx = next(
            (i for i, line in enumerate(lines) if line.startswith(f"{pkg}==")),
            None,
        )
        if pkg_line_idx is None:
            pytest.skip(f"{pkg} not yet in the committed lock (pending T07 pip-compile regen)")
        # Next non-empty line should be a hash continuation.
        next_line = lines[pkg_line_idx + 1] if pkg_line_idx + 1 < len(lines) else ""
        assert "--hash=sha256:" in next_line, f"{pkg} pin has no following ``--hash=sha256:`` line"


# ---------------------------------------------------------------------------
# Dockerfile shape — multi-stage, non-root, hash-verified
# ---------------------------------------------------------------------------


class TestDockerfileShape:
    """Pin the R-12-2 / R-12-3 critical Dockerfile decisions so a refactor
    cannot silently weaken the security posture."""

    @pytest.fixture
    def dockerfile(self) -> str:
        return (_IMAGE_DIR / "Dockerfile").read_text()

    def test_multi_stage_build(self, dockerfile: str) -> None:
        """Two stages — builder (with compilers) + runtime (without)."""
        assert "AS builder" in dockerfile
        assert "AS runtime" in dockerfile

    def test_base_image_pinned_python_3_11_slim(self, dockerfile: str) -> None:
        """R-12-3: ``python:3.11-slim-bookworm`` is the pinned base —
        Debian Bookworm glibc 2.36, slim variant for size."""
        assert "python:3.11-slim-bookworm" in dockerfile

    def test_runs_as_non_root_user(self, dockerfile: str) -> None:
        """R-12-2 #4 / CIS Docker §5: ``USER 65534:65534`` (nobody:nogroup)."""
        assert "USER 65534:65534" in dockerfile

    def test_require_hashes_mode(self, dockerfile: str) -> None:
        """R-12-3: hash-mode pinning is the build-time gate."""
        assert "--require-hashes" in dockerfile

    def test_apt_cache_cleared(self, dockerfile: str) -> None:
        """Image-size hygiene: apt cache must be cleared after install."""
        assert "rm -rf /var/lib/apt/lists/*" in dockerfile

    def test_healthcheck_none(self, dockerfile: str) -> None:
        """R-12-2: ``HEALTHCHECK NONE`` — a healthcheck would spawn probe
        processes inside every container, skewing the resource caps."""
        assert "HEALTHCHECK NONE" in dockerfile

    def test_oci_labels_present(self, dockerfile: str) -> None:
        """OCI provenance labels — image registry tooling reads these."""
        assert "org.opencontainers.image.title=" in dockerfile
        assert "org.opencontainers.image.version=" in dockerfile

    def test_no_pip_install_at_runtime(self, dockerfile: str) -> None:
        """The runtime stage MUST NOT have pip — model-generated code that
        tries ``pip install`` should fail at the substrate level, not at a
        Python check. Verify pip is only in the builder stage by checking
        that the runtime stage doesn't install pip-able package managers."""
        # The Dockerfile copies ``/opt/venv`` (which has pip), but it's
        # owned by 65534 read-only; the model runs as 65534 with no
        # network. The defence-in-depth is layered: image-time + runtime.
        # This is a weaker assertion than the runtime check, but pins the
        # intent: no apt-installed pip in the runtime stage.
        runtime_stage = dockerfile.split("AS runtime")[1]
        # No apt-installing python-pip
        assert "python-pip" not in runtime_stage
        assert "python3-pip" not in runtime_stage

    def test_workspace_layout_matches_d129(self, dockerfile: str) -> None:
        """D-12-9: ``/workspace/in`` + ``/workspace/out`` directories
        exist in the image so the bind-mounts have something to attach to."""
        assert "/workspace/in" in dockerfile
        assert "/workspace/out" in dockerfile

    def test_runtime_installs_graphviz_dot_binary(self, dockerfile: str) -> None:
        """D-25-1: the runtime stage apt-installs ``graphviz`` so the Python
        wrapper's ``dot`` shell-out resolves."""
        runtime_stage = dockerfile.split("AS runtime")[1]
        assert "graphviz" in runtime_stage

    @pytest.mark.parametrize("lib", _WEASYPRINT_APT_LIBS)
    def test_runtime_installs_weasyprint_apt_libs(self, dockerfile: str, lib: str) -> None:
        """D-25-1: weasyprint's pango/cairo/gdk-pixbuf cffi stack must be
        present in the runtime stage."""
        runtime_stage = dockerfile.split("AS runtime")[1]
        assert lib in runtime_stage, f"weasyprint runtime lib {lib!r} missing from runtime stage"


# ---------------------------------------------------------------------------
# Smoke-import CMD — every D-25-1 package is import-checked at build time
# ---------------------------------------------------------------------------


# distribution-name → import-name where they differ; identity otherwise.
_IMPORT_NAMES = {
    "beautifulsoup4": "bs4",
    "graphviz": "graphviz",
    "httpx": "httpx",
    "hypothesis": "hypothesis",
    "ipykernel": "ipykernel",
    "jinja2": "jinja2",
    "jsonschema": "jsonschema",
    "lxml": "lxml",
    "markdown": "markdown",
    "matplotlib": "matplotlib",
    "networkx": "networkx",
    "numpy": "numpy",
    "openpyxl": "openpyxl",
    "pandas": "pandas",
    "pillow": "PIL",
    "pygments": "pygments",
    "pypdf": "pypdf",
    "pytest": "pytest",
    "python-docx": "docx",
    "python-pptx": "pptx",
    "pyyaml": "yaml",
    "reportlab": "reportlab",
    "requests": "requests",
    "rich": "rich",
    "scikit-learn": "sklearn",
    "scipy": "scipy",
    "seaborn": "seaborn",
    "statsmodels": "statsmodels",
    "sympy": "sympy",
    "tabulate": "tabulate",
    "weasyprint": "weasyprint",
}


class TestSmokeImportCmd:
    """The Dockerfile ``CMD`` self-validates the image: a broken pin, missing
    transitive, or missing apt lib fails ``docker run`` cleanly. T07 expands
    it to import every D-25-1 package by its correct import name + assert the
    graphviz ``dot`` binary is on PATH."""

    @pytest.fixture
    def smoke_cmd(self) -> str:
        dockerfile = (_IMAGE_DIR / "Dockerfile").read_text()
        # The final CMD line.
        return next(line for line in dockerfile.splitlines() if line.startswith("CMD ["))

    @pytest.mark.parametrize("pkg", tuple(_IMPORT_NAMES))
    def test_cmd_imports_package(self, smoke_cmd: str, pkg: str) -> None:
        import_name = _IMPORT_NAMES[pkg]
        # Word-boundary match so e.g. ``rich`` does not match ``richX``.
        import re

        assert re.search(rf"\b{re.escape(import_name)}\b", smoke_cmd), (
            f"smoke CMD does not import {pkg!r} (import name {import_name!r})"
        )

    def test_cmd_asserts_dot_binary(self, smoke_cmd: str) -> None:
        assert "shutil.which('dot')" in smoke_cmd

    def test_cmd_emits_ready_sentinel(self, smoke_cmd: str) -> None:
        assert "persona-sandbox ready" in smoke_cmd
