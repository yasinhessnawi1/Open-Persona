"""Shared fixtures for the spec-12 adversarial security suite.

The ``sandbox`` fixture is parametrised over backend kinds; each parametrisation
either yields a real :class:`CodeSandbox` instance OR **skips** when the
backend isn't available:

- ``"local_docker"`` — wired in T05a. Yields a :class:`LocalDockerSandbox`
  when the Docker daemon is reachable (per
  :func:`is_docker_available`); skips otherwise. T05b/T07 extend the
  hardening but the fixture wiring is stable from T05a on.
- ``"hosted"`` — wired by T08. Skipped until the D-12-12 lock-gates
  measure clean against a registered E2B Hobby account.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from persona.sandbox.local_docker import (
    DEFAULT_IMAGE,
    LocalDockerSandbox,
    is_docker_available,
)


def _is_image_available(tag: str) -> bool:
    """True if the sandbox image is locally available.

    T06 builds ``persona-sandbox:0.1.0``. Until T06 ships (or the human
    builds the image manually), the security tests skip rather than fail —
    the failure mode is environmental, not a security regression."""
    import docker
    from docker.errors import DockerException, ImageNotFound

    try:
        client = docker.from_env()
        try:
            client.images.get(tag)
        except ImageNotFound:
            return False
        finally:
            client.close()
    except DockerException:
        return False
    return True


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from persona.sandbox import CodeSandbox


@pytest_asyncio.fixture(params=["local_docker", "hosted"])
async def sandbox(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> AsyncIterator[CodeSandbox]:
    """Yield a real :class:`CodeSandbox` for the security suite.

    - ``local_docker``: yields :class:`LocalDockerSandbox` if the Docker
      daemon is reachable. Skips otherwise (CI runners without Docker, dev
      machines with the daemon stopped). The sandbox image
      ``persona-sandbox:0.1.0`` (T06) must also be available; if it's not,
      the per-attack execution raises :class:`SandboxUnavailableError` with
      a clear "build the image" message and the suite reports a failure
      rather than a silent pass.
    - ``hosted``: skips with the T08-blocked-on-D-12-12 message until E2B
      registration completes.
    """
    backend = request.param
    if backend == "local_docker":
        if not is_docker_available():
            pytest.skip(
                "Docker daemon unreachable; LocalDockerSandbox security tests "
                "require a running Docker daemon. Install Docker and rerun."
            )
        if not _is_image_available(DEFAULT_IMAGE):
            pytest.skip(
                f"Sandbox image {DEFAULT_IMAGE!r} not built. "
                "Build it via the T06 Dockerfile (or pull it) before running "
                "the LocalDockerSandbox security suite."
            )
        workspace = tmp_path_factory.mktemp("sandbox-secsuite")
        sandbox: CodeSandbox = LocalDockerSandbox(workspace_root=workspace)
        try:
            yield sandbox
        finally:
            await sandbox.aclose()
        return
    if backend == "hosted":
        pytest.skip(
            "HostedSandbox not yet implemented (T08). "
            "Blocked on D-12-12 lock-gates + human E2B Hobby registration."
        )
    raise AssertionError(f"unknown backend: {backend!r}")  # pragma: no cover
