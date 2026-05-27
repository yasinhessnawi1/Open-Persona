"""Shared test fixtures for persona-core."""

from __future__ import annotations

import pytest
from persona.audit import MemoryAuditLogger


@pytest.fixture()
def memory_audit_logger() -> MemoryAuditLogger:
    """A fresh in-memory audit logger for dependent-component tests."""
    return MemoryAuditLogger()
