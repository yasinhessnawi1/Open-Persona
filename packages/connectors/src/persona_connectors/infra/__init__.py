"""API-coupled infrastructure adapters for the connector framework (Spec C1).

This subpackage holds the concrete adapters that implement the owned-surface
ports (``persona_connectors.domain``) over persona-api's DB layer — the C0
"Protocol in the owned surface, adapter in infra" pattern. Modules here MAY
import ``persona_api`` (the tables, the RLS / dispatch engines, the
``current_user_id`` contextvar); the decoupling guard allow-lists this package
exactly so the owned surface stays api-free and a future extract-to-core is a
dependency swap, not a reshape.
"""

from __future__ import annotations

from persona_connectors.infra.conversation_store import PostgresConversationStateStore
from persona_connectors.infra.link_store import PostgresLinkStore

__all__ = ["PostgresConversationStateStore", "PostgresLinkStore"]
