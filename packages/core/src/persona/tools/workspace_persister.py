"""The ``WorkspacePersister`` port — persist tool byte-output to the workspace.

Spec 28 (Rich Tool Output Delivery). This is the **port** in the hexagonal
design (D-28-X-persisted-artifact-shape, tension #4): persona-core defines the
Protocol with zero storage dependencies; persona-api supplies the concrete
adapter that wraps the proven ``_persist_bytes`` recipe (blake2b
content-addressing + ``O_NOFOLLOW`` write + F5 ``.f5.json`` sidecar) and is
RLS-scoped to the persona owner (D-28-X-persister-user-scope).

Byte-producing tools (``generate_image`` / ``file_write`` / ``render_diagram``)
accept an **optional** ``WorkspacePersister``; when ``None`` the tool produces
its pre-Spec-28 :class:`ToolResult` unchanged (empty ``artifacts`` tuple) so
existing callers and tests are byte-identical (backward-compat / acceptance
criterion #9). When present, the tool calls :meth:`persist` and puts the
returned :class:`PersistedArtifact` on :attr:`ToolResult.artifacts`.

The sandbox (``code_execution``) keeps its own remote-file-copy callback
(D-17-X) and does **not** route through this bytes-in-memory port — it only
*surfaces* its produced files into the same ``artifacts`` tuple
(D-28-X-sandbox-consolidation-scope).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from persona.schema.tools import PersistedArtifact

__all__ = ["WorkspacePersister"]


@runtime_checkable
class WorkspacePersister(Protocol):
    """Persist in-memory bytes to the persona workspace; return its identity.

    Implementations write the bytes durably (content-addressed) and may write
    an F5 metadata sidecar, then return the resolved :class:`PersistedArtifact`.
    A persist failure should raise; the calling tool converts it to a structured
    ``ToolResult(is_error=True, ...)`` so the model can recover (the bytes are
    the primary deliverable, but a failure must never crash the turn).
    """

    async def persist(
        self,
        data: bytes,
        *,
        mime_type: str,
        suggested_filename: str,
    ) -> PersistedArtifact:
        """Persist ``data`` and return its :class:`PersistedArtifact`.

        Args:
            data: Raw bytes to persist.
            mime_type: IANA media type of ``data`` (drives the on-disk
                extension and the downstream renderer dispatch).
            suggested_filename: A human-meaningful name hint (e.g.
                ``"report.md"``); the adapter may content-address the on-disk
                name and is free to ignore this beyond extension inference.

        Returns:
            The persisted artifact's workspace identity.
        """
        ...
