"""The owner-scoped job execution context handed to handlers (Spec A0, T4).

:class:`WorkerJobContext` is the concrete :class:`persona.jobs.JobContext` the
worker hands to a handler. It exposes ONLY owner-scoped database access — the
``persona_app`` RLS engine bound to the job's owner — and never the worker's
cross-tenant dispatch engine. A handler therefore has no in-band path to another
tenant's data: the RLS boundary is structural (D-A0-X-rls-chokepoint), proven by
the adversarial cross-tenant test, not asserted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona_api.db.engine import rls_connection
from persona_api.services import audit_service

if TYPE_CHECKING:
    from collections.abc import Mapping
    from contextlib import AbstractContextManager

    from sqlalchemy import Connection, Engine

__all__ = ["WorkerJobContext"]


class WorkerJobContext:
    """Owner-scoped execution context for one job (satisfies ``JobContext``).

    Holds the ``persona_app`` RLS engine and the job's ``owner_id`` only. Every
    connection it yields is RLS-bound to that owner via
    :func:`~persona_api.db.engine.rls_connection`, so a handler's queries can
    never reach another tenant. The cross-tenant dispatch engine is held by the
    worker loop and is deliberately absent here.
    """

    def __init__(self, *, owner_id: str, rls_engine: Engine, job_id: str, job_type: str) -> None:
        self._owner_id = owner_id
        self._rls_engine = rls_engine
        self._job_id = job_id
        self._job_type = job_type

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def job_id(self) -> str:
        return self._job_id

    def connection(self) -> AbstractContextManager[Connection]:
        """Yield an owner-scoped ``persona_app`` connection (transaction-scoped GUC)."""
        return rls_connection(self._rls_engine, self._owner_id)

    def meter(
        self, *, amount_micros: int, kind: str, detail: Mapping[str, str] | None = None
    ) -> None:
        """Record a job-attributed spend event into ``audit_log`` (no new table).

        Reuses the existing audit machinery (``action='job.spend'``,
        ``target=job_id``) so spend is queryable + attributable per job without
        forking the observability schema (D-A0-X-metering-bar). ``persona_app`` has
        INSERT on ``audit_log``; the row carries the owner explicitly (non-RLS).
        Best-effort by construction (``audit_service.record`` swallows failures).
        """
        metadata: dict[str, str] = {
            "job_type": self._job_type,
            "kind": kind,
            "amount_micros": str(amount_micros),
        }
        if detail:
            metadata.update(detail)
        audit_service.record(
            engine=self._rls_engine,
            user_id=self._owner_id,
            action="job.spend",
            target=self._job_id,
            metadata=metadata,
        )
