"""Unit tests for the spec-12 T06 sandbox image manifest.

These tests run unconditionally on the default suite — they verify the
**static manifest** (Dockerfile syntax, requirements.in contents,
README presence) without invoking ``docker build``. The actual image
build is integration territory (``docker build`` requires a daemon +
network + minutes of wall-clock); it's a manual / CI step per the
[image README](packages/core/src/persona/sandbox/image/README.md).

The D-12-2 / R-12-3 manifest is pinned so a refactor cannot silently
drop a Spec-16 or Spec-17 dependency.
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
# D-12-2 / R-12-3 manifest — every required package is pinned
# ---------------------------------------------------------------------------


_REQUIRED_PACKAGES = (
    # Spec 17 — data analysis
    "numpy",
    "pandas",
    "matplotlib",
    # Spec 16 — document generation
    "openpyxl",
    "python-docx",
    "python-pptx",
    "reportlab",
    # D-12-1 v0.2 kernel path
    "ipykernel",
)


class TestRequirementsInManifest:
    """The R-12-3 manifest is pinned. A refactor that drops a Spec-16/17
    dep would silently break the downstream specs without these tests."""

    @pytest.fixture
    def requirements_in(self) -> str:
        return (_IMAGE_DIR / "requirements.in").read_text()

    @pytest.mark.parametrize("pkg", _REQUIRED_PACKAGES)
    def test_required_package_is_pinned(self, requirements_in: str, pkg: str) -> None:
        # Pin syntax: ``pkg==X.Y.Z`` (exact). Loose ``pkg>=X`` is rejected
        # by ``pip install --require-hashes`` anyway, so this test catches
        # the regression at write time.
        assert f"{pkg}==" in requirements_in, (
            f"required package {pkg!r} missing or not exact-pinned in "
            f"requirements.in — D-12-2 / R-12-3 mandate"
        )


class TestRequirementsTxtHashMode:
    """Hash-mode is the strongest pip-level supply-chain guarantee. The
    committed ``requirements.txt`` ships with placeholders that ``pip-compile``
    fills in on the build host — these tests verify the placeholders exist
    so the build doesn't silently skip hash mode."""

    @pytest.fixture
    def requirements_txt(self) -> str:
        return (_IMAGE_DIR / "requirements.txt").read_text()

    def test_uses_hash_syntax(self, requirements_txt: str) -> None:
        """``--hash=sha256:...`` syntax is present for the top-level pkgs."""
        assert "--hash=sha256:" in requirements_txt

    @pytest.mark.parametrize("pkg", _REQUIRED_PACKAGES)
    def test_required_package_has_hash_block(self, requirements_txt: str, pkg: str) -> None:
        # Each required package has a ``pkg==X.Y.Z\n    --hash=sha256:...`` block.
        assert f"{pkg}==" in requirements_txt
        # Find the line index of the pkg pin and verify a hash follows.
        lines = requirements_txt.splitlines()
        pkg_line_idx = next(
            (i for i, line in enumerate(lines) if line.startswith(f"{pkg}==")),
            None,
        )
        assert pkg_line_idx is not None
        # Next non-empty line should be a hash continuation
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
