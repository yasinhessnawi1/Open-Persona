"""Reserved seam — per-user gateway secret injection (Spec N1 → N4, D-N1-5).

**DEFINED NOW, IMPLEMENTED NEVER IN v1** — the A2 ``on_event``-seam discipline (cf.
D-A2-5): fix the interface + the isolation contract now so N4 slots an implementation
in additively, but ship no implementation and no caller.

Why there is no live per-user path in N1: N1 is **connect-only** to a *deployment-level*
gateway, whose only live credential is the operator bearer
(``PERSONA_DOCKER_MCP_GATEWAY_TOKEN``). A **shared** gateway cannot accept a different
per-persona / per-user secret, so per-user secret injection is **not deliverable by
connect-only** and is explicitly out of N1's live path. The mirror's
:class:`~persona.tools.mcp.catalog.MCPSecretField` is **display-only** metadata (which
secret a server needs) the apps UX renders — never resolved into an injected credential
here.

What N4 will build (against this seam): a per-user path — **user → encrypted secret
store → a per-user / curated gateway**, supplying the value out-of-band. It reuses the
Spec-30 Fernet store + **its existing key (NO second key)**, and upholds the same
isolation invariant this spec proves for the bearer: the secret value NEVER enters a
model turn, tool spec, tool result, or audit line.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from persona.tools.mcp.catalog import MCPSecretField

__all__ = ["GatewaySecretResolver"]


@runtime_checkable
class GatewaySecretResolver(Protocol):
    """RESERVED (N4): resolve a per-user secret value for a gateway server.

    A future N4 implementation maps ``(owner_id, server_name, field)`` → the stored
    secret VALUE, fetched from the encrypted per-user store and handed to a per-user /
    curated gateway out-of-band. It MUST NOT return the value through, or place it in,
    any model-facing surface — the D-N1-5 isolation invariant the bearer path is proven
    against in T5 holds here too. **No implementation is registered in v1**; nothing in
    the connect path references this Protocol.
    """

    def resolve(self, *, owner_id: str, server_name: str, field: MCPSecretField) -> str | None:
        """Return the stored secret value for this user+server+field, or ``None``.

        RESERVED — N4 implements this against the Spec-30 Fernet store (existing key).
        """
        ...
