"""Shared fixtures for persona-voice integration tests (spec V1 T08/T11/T12).

The integration suite requires:

* **LiveKit Server** running on ``ws://localhost:7880`` with dev keys
  ``devkey`` / ``secret`` (the canonical dev-mode invocation:
  ``docker run --rm -p 7880:7880 -p 7881:7881 -p 7882-7882/udp
  livekit/livekit-server:latest --dev --bind 0.0.0.0``).
* **Postgres** at ``postgresql://persona:persona@localhost:5436/persona``
  (or whichever port the dev compose stack exposes — adjust env vars).

Tests skip automatically if either service is unreachable so the integration
suite stays runnable in CI even when only one substrate is provisioned.
"""

from __future__ import annotations

import asyncio
import os
import socket

import pytest


def _tcp_reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    """Return True if ``host:port`` accepts a TCP connection within ``timeout``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def livekit_url() -> str:
    return os.environ.get("PERSONA_VOICE_TEST_LIVEKIT_URL", "ws://localhost:7880")


@pytest.fixture(scope="session")
def livekit_api_key() -> str:
    return os.environ.get("PERSONA_VOICE_TEST_LIVEKIT_API_KEY", "devkey")


@pytest.fixture(scope="session")
def livekit_api_secret() -> str:
    return os.environ.get("PERSONA_VOICE_TEST_LIVEKIT_API_SECRET", "secret")


@pytest.fixture(scope="session", autouse=False)
def require_livekit_server(livekit_url: str) -> None:
    """Skip the test if LiveKit Server is not reachable on the configured URL."""
    # Parse "ws://host:port" → ("host", port)
    parsed = livekit_url.removeprefix("ws://").removeprefix("wss://")
    host, _, port_s = parsed.partition(":")
    port = int(port_s) if port_s else 7880
    if not _tcp_reachable(host, port):
        pytest.skip(
            f"LiveKit Server not reachable at {livekit_url}; "
            "start with: docker run --rm -p 7880:7880 -p 7881:7881 "
            "-p 7882:7882/udp livekit/livekit-server:latest --dev --bind 0.0.0.0",
            allow_module_level=False,
        )


@pytest.fixture
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    """Per-test asyncio loop policy (Spec V1 T08/T11/T12 integration tests
    spawn tasks across the test body; isolating the loop prevents leakage
    between tests)."""
    return asyncio.DefaultEventLoopPolicy()
