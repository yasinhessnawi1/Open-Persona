"""The connector-service composition root (Spec C1 T1, C1-D-1).

This is the **single api-coupled module** in persona-connectors. Per C1-D-1 the
connector reuses persona-api's reply-producing chat flow + C0's delivery router
in-process, following the ``run_worker.py`` pattern — a separate long-lived
process that imports api services and sets the ``current_user_id`` RLS contextvar
per unit of work, outside any FastAPI request scope. Concentrating the
``persona_api`` import here keeps the owned surface (:mod:`persona_connectors.domain`)
import-decoupled, so a future extract-to-core is a dependency swap, not a reshape
(the reversibility guarantee).

T1 wires the **shared foundations** every later task needs: the edition switch,
the RLS engine, and the owner-scope (D-C1-X-rls-spine). The delivery-router
(C0 ``DeliveryRouter`` reuse, T10) and the conversation-loop builder (api's
``RuntimeFactory``, T9) plug in here in their tasks — their seams are marked
below.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from persona_api.config import Edition
from persona_api.db.community import make_community_engine
from persona_api.db.engine import create_db_engine
from persona_api.middleware.rls_context import current_user_id, make_rls_engine

from persona_connectors.errors import ConnectorError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

    from persona_connectors.config import ConnectorConfig

__all__ = ["ConnectorComposition"]


class ConnectorComposition:
    """Assembles the connector service's shared foundations from its config.

    Holds no DB connection at construction (the engine is built lazily via
    :meth:`make_engine`); holds no global state. Dependency injection via the
    constructor (no globals — ENG-STD).
    """

    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config

    @property
    def config(self) -> ConnectorConfig:
        """The service configuration this root was built from."""
        return self._config

    @property
    def edition(self) -> Edition:
        """The open-core edition (Spec 33), as the persona-api ``Edition`` enum."""
        return Edition(self._config.edition.strip().lower())

    def make_engine(self) -> Engine:
        """Build the edition-appropriate RLS-scoped engine (lazy — no connection).

        Cloud: a Postgres RLS engine (the Spec 08 D-08-1 checkout/checkin listener
        scopes every connection by ``current_user_id``). Community: the single-
        owner local engine (Spec 33). Fails fast on a cloud edition with no
        ``database_url`` — a misconfiguration caught at the boundary, not three
        layers deep.

        Returns:
            The SQLAlchemy :class:`~sqlalchemy.engine.Engine`. Connection happens
            lazily on first use, RLS-scoped by the owner contextvar set in
            :meth:`owner_scope`.

        Raises:
            ConnectorError: Cloud edition with no ``database_url`` configured.
        """
        if self.edition is Edition.community:
            return make_community_engine(Path(self._config.community_db_path))
        if not self._config.database_url:
            raise ConnectorError(
                "cloud edition requires a database_url",
                context={"edition": self.edition.value},
            )
        return make_rls_engine(self._config.database_url, pool_size=self._config.db_pool_size)

    def make_dispatch_engine(self) -> Engine:
        """Build the cross-tenant dispatch engine for the pre-auth resolve/redeem reads.

        An inbound arrives from an *unauthenticated* platform identity, so resolving
        ``(platform, sender_id) → owner`` and redeeming a link token are reads that
        precede any owner scope — they run BYPASSRLS on this engine, keyed by the
        ``UNIQUE`` spine / the unguessable token hash (the A0-worker pre-auth
        pattern, D-C1-5). After resolution, downstream work runs owner-scoped via
        :meth:`owner_scope` on the RLS engine. Community (single owner, no RLS) can
        reuse the same engine for both roles.

        Raises:
            ConnectorError: Cloud edition with no ``database_url`` configured.
        """
        if self.edition is Edition.community:
            return make_community_engine(Path(self._config.community_db_path))
        if not self._config.database_url:
            raise ConnectorError(
                "cloud edition requires a database_url for the dispatch engine",
                context={"edition": self.edition.value},
            )
        return create_db_engine(self._config.database_url)

    @contextlib.contextmanager
    def owner_scope(self, owner_id: str) -> Iterator[None]:
        """Scope a unit of work to ``owner_id`` (the run_worker.py RLS spine).

        Sets the persona-api ``current_user_id`` contextvar the RLS engine's
        checkout listener reads, so every store read/write inside the scope is
        owner-scoped exactly as the web request path is (D-C1-X-rls-spine); resets
        it in a ``finally`` so an error never leaks the owner to the next message.
        The connector flow (T9) enters this scope after resolving the inbound
        platform identity to its linked Persona user.
        """
        token = current_user_id.set(owner_id)
        try:
            yield
        finally:
            current_user_id.reset(token)
