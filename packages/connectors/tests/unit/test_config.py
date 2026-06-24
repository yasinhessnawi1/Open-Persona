"""ConnectorConfig — env-driven settings for the persona-connectors service (C1 T1).

Twelve-factor discipline (the Spec 08 APIConfig / V1 VoiceConfig precedent): every
knob lands via environment variables, prefixed ``PERSONA_CONNECTORS_``; the
edition reads the shared, prefix-less ``PERSONA_EDITION`` (Spec 33). The idle
timeout default is C1-D-3 (30 minutes).
"""

from __future__ import annotations

import pytest
from persona_connectors.config import ConnectorConfig


def test_idle_timeout_default_is_30_minutes() -> None:
    """C1-D-3: the idle-timeout default is 30 minutes (low-stakes, tunable)."""
    config = ConnectorConfig()
    assert config.idle_timeout_minutes == 30


def test_idle_timeout_is_env_tunable() -> None:
    """C1-D-3: tunable via PERSONA_CONNECTORS_IDLE_TIMEOUT_MINUTES."""
    config = ConnectorConfig(idle_timeout_minutes=90)
    assert config.idle_timeout_minutes == 90


def test_idle_timeout_must_be_positive() -> None:
    """A non-positive idle timeout is a misconfiguration — fail fast at the boundary."""
    with pytest.raises(ValueError):  # noqa: PT011 — pydantic raises ValidationError (a ValueError)
        ConnectorConfig(idle_timeout_minutes=0)


def test_edition_defaults_to_community() -> None:
    """Zero-infra default (Spec 33): community, single local owner, no auth."""
    config = ConnectorConfig()
    assert config.edition == "community"
    assert config.is_cloud is False


def test_edition_reads_shared_persona_edition_alias() -> None:
    """The edition reads the prefix-less PERSONA_EDITION var (shared with api/web/voice)."""
    config = ConnectorConfig(edition="cloud")
    assert config.edition == "cloud"
    assert config.is_cloud is True


def test_database_url_defaults_empty() -> None:
    """No DB configured by default — the composition root tolerates absence (T1)."""
    config = ConnectorConfig()
    assert config.database_url == ""
