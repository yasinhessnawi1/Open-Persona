"""The Docker MCP Gateway edition posture (Spec N1, D-N1-7).

The honest edition split for the gateway, gated off the existing ``PERSONA_EDITION``
flag (Spec 33, D-33-1) — no parallel mechanism:

- **community / local** — the gateway integration is fully enabled. The operator runs
  their own Docker + gateway, single-owner, owns the trust choice; Docker isolates. The
  genuine "enable once → every persona has it" win. **No gate.**
- **cloud / hosted** — connecting a gateway is **connect-only to an operator-run,
  operator-VETTED gateway whose aggregated tools are SHARED across tenants**. Per-tenant
  gateways, per-tenant container-running, and per-user secret injection are **deferred**
  (D-N1-7) — so a single shared gateway exposes its tools to every tenant's opted-in
  personas. Because that is a deliberate trust decision, the operator MUST explicitly
  acknowledge the vetted-shared posture via ``PERSONA_ALLOW_CLOUD_GATEWAY=1`` — mirroring
  the D-33-4 public-noauth guard exactly. Without the ack + a gateway URL set, the API
  **refuses to start**; with the ack it warns (posture recorded) and proceeds.

This is the security model acceptance criterion #5 made concrete: **no arbitrary
third-party-container execution in the hosted product without vetting** — the ack flag
is the operator asserting the gateway is vetted. Connect-only itself never runs
containers (the gateway does), so there is no per-tenant execution to gate beyond this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from persona.config import PersonaCoreConfig
from persona.logging import get_logger

from persona_api.config import Edition
from persona_api.errors import CloudGatewayNotVettedError

if TYPE_CHECKING:
    from persona_api.config import APIConfig

__all__ = ["check_gateway_edition_posture"]

_LOG = get_logger("api.editions.gateway_guard")


def check_gateway_edition_posture(config: APIConfig, *, gateway_url: str | None = None) -> None:
    """Enforce the cloud gateway vetting ack at startup (D-N1-7).

    Args:
        config: The API config (its ``edition`` + ``allow_cloud_gateway`` drive the gate).
        gateway_url: The configured Docker MCP Gateway URL. Defaults to reading
            :class:`PersonaCoreConfig` from the environment (the gateway URL is a
            core-config knob, D-N1-8); injected in tests.

    Raises:
        CloudGatewayNotVettedError: cloud edition + a gateway URL set +
            ``PERSONA_ALLOW_CLOUD_GATEWAY`` unset.
    """
    url = gateway_url if gateway_url is not None else PersonaCoreConfig().docker_mcp_gateway_url
    if not url:
        return  # no gateway configured — nothing to gate (fail-soft)
    if config.edition is not Edition.cloud:
        return  # community: full local integration; the user owns the trust choice
    if config.allow_cloud_gateway:
        _LOG.warning(
            "cloud Docker MCP Gateway enabled: connect-only to a VETTED gateway SHARED "
            "across tenants (PERSONA_ALLOW_CLOUD_GATEWAY set). Per-tenant gateways, "
            "container-running, and per-user secret injection are DEFERRED (D-N1-7)."
        )
        return
    raise CloudGatewayNotVettedError(
        "refusing to start: PERSONA_EDITION=cloud with PERSONA_DOCKER_MCP_GATEWAY_URL set "
        "but PERSONA_ALLOW_CLOUD_GATEWAY unset. A cloud gateway is shared across tenants "
        "and must be operator-vetted; set PERSONA_ALLOW_CLOUD_GATEWAY=1 to acknowledge, "
        "or unset the gateway URL.",
        context={"edition": config.edition.value},
    )
