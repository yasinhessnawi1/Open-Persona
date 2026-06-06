"""Unit tests for ``persona_api.sandbox.config.SandboxPoolConfig`` (spec 12 T09b).

Verifies the D-12-17 v0.1 defaults, the env-var prefix wiring, and the
warm-pool-locked-to-zero validator that future-proofs the env shape while
making the v0.1 implementation gap explicit.
"""

from __future__ import annotations

import pytest
from persona_api.sandbox.config import SandboxPoolConfig
from pydantic import ValidationError


def test_defaults_match_d_12_17() -> None:
    cfg = SandboxPoolConfig(_env_file=None)  # type: ignore[call-arg]
    assert cfg.warm_pool_size == 0
    assert cfg.reap_interval_s == 60.0
    assert cfg.idle_timeout_s == 300.0
    assert cfg.max_per_user == 2


def test_env_vars_read_via_persona_sandbox_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSONA_SANDBOX_REAP_INTERVAL_S", "30")
    monkeypatch.setenv("PERSONA_SANDBOX_IDLE_TIMEOUT_S", "600")
    monkeypatch.setenv("PERSONA_SANDBOX_MAX_PER_USER", "5")
    cfg = SandboxPoolConfig()
    assert cfg.reap_interval_s == 30.0
    assert cfg.idle_timeout_s == 600.0
    assert cfg.max_per_user == 5


def test_warm_pool_size_locked_to_zero_at_v01(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-12-17 v0.1 lock: nonzero warm-pool size needs the maintainer task."""
    monkeypatch.setenv("PERSONA_SANDBOX_WARM_POOL_SIZE", "2")
    with pytest.raises(ValidationError, match="warm-pool maintainer"):
        SandboxPoolConfig()


def test_warm_pool_size_zero_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSONA_SANDBOX_WARM_POOL_SIZE", "0")
    cfg = SandboxPoolConfig()
    assert cfg.warm_pool_size == 0


def test_warm_pool_size_negative_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONA_SANDBOX_WARM_POOL_SIZE", "-1")
    with pytest.raises(ValidationError, match=">= 0"):
        SandboxPoolConfig()


def test_reap_interval_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONA_SANDBOX_REAP_INTERVAL_S", "0")
    with pytest.raises(ValidationError, match="> 0"):
        SandboxPoolConfig()


def test_idle_timeout_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONA_SANDBOX_IDLE_TIMEOUT_S", "-1")
    with pytest.raises(ValidationError, match="> 0"):
        SandboxPoolConfig()


def test_max_per_user_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSONA_SANDBOX_MAX_PER_USER", "0")
    with pytest.raises(ValidationError, match=">= 1"):
        SandboxPoolConfig()
