"""Domain exceptions for the connector framework (Spec C1).

Per ENGINEERING_STANDARDS: domain logic raises domain exceptions, never bare
``ValueError``/``RuntimeError``; every exception carries a ``context: dict[str,
str]`` keyword so log records stay structured. ``ConnectorError`` is the C1 base
and extends persona-core's :class:`~persona.errors.PersonaError` so the whole
stack catches one hierarchy.

More specific exceptions (e.g. an unlinked-identity error, a persona-not-
addressable error) land in later tasks where they are raised — this module ships
the base so T1's surface is real. Import-decoupled from persona_api.
"""

from __future__ import annotations

from persona.errors import PersonaError

__all__ = [
    "ConnectorError",
    "IdentityNotLinkedError",
    "LinkTokenInvalidError",
]


class ConnectorError(PersonaError):
    """Base for every connector-framework domain error (Spec C1).

    Subclasses are introduced where they are raised (identity resolution,
    persona addressing, account linking, delivery). Carries the inherited
    ``context: dict[str, str]`` for structured logging.
    """


class IdentityNotLinkedError(ConnectorError):
    """An inbound platform identity has no live (active) Persona-user binding.

    The load-bearing security invariant (C1-D-5, criteria 6/7): an unlinked (or
    revoked) identity gets a link-instruction and ZERO access — never another
    user's personas. The flow catches this and replies with the linking prompt.
    """


class LinkTokenInvalidError(ConnectorError):
    """A presented link token is unknown, expired, already consumed, or mismatched.

    Single-use + short-TTL + platform-bound (C1-D-5); any violation fails loud so
    a replayed/forged/stale token never binds an identity.
    """
