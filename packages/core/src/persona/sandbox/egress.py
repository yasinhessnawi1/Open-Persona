"""Substrate-level egress filter for the sandbox network (spec 12 T07 / R-12-5).

When a persona enables network access (``NetworkPolicy.enabled=True`` with
an ``allowed_hosts`` allow-list), the substrate MUST STILL block, REGARDLESS
of the allow-list:

- IPv4 link-local ``169.254.0.0/16`` — includes the cloud metadata endpoint
  ``169.254.169.254`` used by AWS/GCP/Azure to expose IAM credentials.
- IPv6 ``fe80::/10`` link-local; IPv6 ULA ``fc00::/7`` (AWS IMDS also
  reachable via IPv6 on some configurations).
- RFC-1918 private ranges: ``10.0.0.0/8``, ``172.16.0.0/12``, ``192.168.0.0/16``.
- Loopback ``127.0.0.0/8`` and ``::1/128``.
- IPv4 broadcast + multicast / IPv6 multicast.
- CGNAT ``100.64.0.0/10`` (RFC 6598 — often used by cloud LB internal hops).
- IPv4-mapped-IPv6 ``::ffff:0:0/96`` — defeats the v4-via-v6 bypass.

The block list applies FIRST, before any allow-list entry. The lesson is
from **spec-11's SSRF finding** (D-11-6): block by the **resolved IP** at
packet time, never trust the allow-list to cover the substrate-level
deny-list (the per-spec attack writes ``169.254.169.254`` into the
allow-list explicitly and the test must still pass).

This module produces the iptables / ip6tables commands that target the
``DOCKER-USER`` chain — Docker's stable extension point. The commands
themselves require root + a running Docker daemon to apply; production
deployment runs ``apply_egress_rules`` once at host setup. T07 ships the
commands as data so tests can verify the rule list without running root.

**D-12-13 reminder:** R-12-5 egress filter is for ``LocalDockerSandbox``.
``HostedSandbox`` (T08, behind D-12-12 E2B lock-gates) uses E2B's native
``update_network()`` API for the substrate-level egress; the BLOCKED_*
catalogs below remain the canonical reference for what to deny.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from persona.logging import get_logger
from persona.sandbox.errors import SandboxUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "BLOCKED_IPV4",
    "BLOCKED_IPV6",
    "SANDBOX_BRIDGE_NAME",
    "apply_egress_rules",
    "build_iptables_rules",
    "build_ip6tables_rules",
]

_logger = get_logger("sandbox.egress")


#: Custom Docker bridge name used by :class:`LocalDockerSandbox` when
#: ``NetworkPolicy.enabled=True``. The iptables rules target traffic
#: leaving this bridge specifically — other Docker containers on the host
#: are unaffected.
SANDBOX_BRIDGE_NAME = "persona-sandbox-net"


#: IPv4 ranges blocked by the substrate egress filter (R-12-5).
#:
#: Order is meaningful for documentation only — iptables evaluates rules
#: in chain order, but every rule in this list is an independent DROP so
#: any ordering produces equivalent behaviour.
#:
#: **Mirror discipline:** ``scripts/setup-sandbox-net.sh`` carries a
#: bash array with the same CIDRs. When adding/removing a range here,
#: update the script too. The script is self-contained (no Python deps —
#: it runs under sudo where the project venv isn't on PATH); diff against
#: the script is the v0.1 cross-check.
BLOCKED_IPV4: tuple[str, ...] = (
    "127.0.0.0/8",  # Loopback (RFC 1122)
    "10.0.0.0/8",  # RFC 1918 private (Class A)
    "172.16.0.0/12",  # RFC 1918 private (Class B)
    "192.168.0.0/16",  # RFC 1918 private (Class C)
    "169.254.0.0/16",  # RFC 3927 link-local — includes 169.254.169.254 IMDS
    "100.64.0.0/10",  # RFC 6598 CGNAT
    "192.0.0.0/24",  # RFC 6890 IETF protocol assignments
    "192.0.2.0/24",  # RFC 6890 TEST-NET-1
    "198.51.100.0/24",  # RFC 6890 TEST-NET-2
    "203.0.113.0/24",  # RFC 6890 TEST-NET-3
    "198.18.0.0/15",  # RFC 2544 benchmarking
    "224.0.0.0/4",  # RFC 5771 multicast
    "240.0.0.0/4",  # RFC 1112 reserved (includes 255.255.255.255 broadcast)
    "0.0.0.0/8",  # RFC 1122 "this network"
)


#: IPv6 ranges blocked by the substrate egress filter (R-12-5).
#:
#: The ``::ffff:0:0/96`` IPv4-mapped row is the easy-to-miss one — without it,
#: an attacker connecting via ``socket.AF_INET6`` to ``::ffff:169.254.169.254``
#: reaches IMDS through the v6 stack, bypassing the v4 rules. Explicit row.
BLOCKED_IPV6: tuple[str, ...] = (
    "::1/128",  # Loopback (RFC 4291)
    "fc00::/7",  # Unique-local (RFC 4193 — includes AWS IPv6 IMDS fd00:ec2::254)
    "fe80::/10",  # Link-local (RFC 4291)
    "ff00::/8",  # Multicast (RFC 4291)
    "2001:db8::/32",  # Documentation (RFC 3849)
    "64:ff9b::/96",  # NAT64 (RFC 6052)
    "::ffff:0:0/96",  # IPv4-mapped (defeats v4-via-v6 bypass)
    "2002::/16",  # 6to4 (RFC 3056 — can encapsulate private v4)
)


def build_iptables_rules(bridge_name: str = SANDBOX_BRIDGE_NAME) -> list[list[str]]:
    """Build the IPv4 iptables commands that block egress from the sandbox bridge.

    Returns a list of argv lists ready for :func:`subprocess.run`. Rule order:

    1. Conntrack ACCEPT for established/related (inserted at position 1 so
       return traffic for any user-allow-list entries passes).
    2. DROP every CIDR in :data:`BLOCKED_IPV4` (substrate-level deny-list;
       defence-in-depth, fires regardless of the user's allow-list).
    3. (User's allow-list ACCEPT rules — appended at runtime by the
       caller, not by this function; the runtime composition root inserts
       them between #2 and #4.)
    4. Default DROP — sandbox traffic not explicitly accepted is dropped.

    Args:
        bridge_name: The custom Docker bridge to filter. Defaults to
            :data:`SANDBOX_BRIDGE_NAME`.

    Returns:
        Argv lists ready for ``subprocess.run`` — caller applies via
        :func:`apply_egress_rules` (requires root).
    """
    rules: list[list[str]] = [
        # 1. Conntrack ACCEPT for return traffic (position 1 so it fires
        #    before the broad DROP). Without this, the host can never
        #    receive replies to user-allow-list-allowed outbound flows.
        [
            "iptables",
            "-I",
            "DOCKER-USER",
            "1",
            "-i",
            bridge_name,
            "-m",
            "conntrack",
            "--ctstate",
            "ESTABLISHED,RELATED",
            "-j",
            "ACCEPT",
            "-m",
            "comment",
            "--comment",
            "persona-sandbox: return traffic",
        ],
    ]
    # 2. DROP every blocked CIDR.
    rules.extend(
        [
            "iptables",
            "-A",
            "DOCKER-USER",
            "-i",
            bridge_name,
            "-d",
            cidr,
            "-j",
            "DROP",
            "-m",
            "comment",
            "--comment",
            f"persona-sandbox: block {cidr}",
        ]
        for cidr in BLOCKED_IPV4
    )
    # 4. Default DROP — at the tail so any explicit ACCEPTs the runtime
    #    inserts above this rule (the user's allow-list, appended between
    #    #2 and #4 by the composition root) take effect first.
    rules.append(
        [
            "iptables",
            "-A",
            "DOCKER-USER",
            "-i",
            bridge_name,
            "-j",
            "DROP",
            "-m",
            "comment",
            "--comment",
            "persona-sandbox: default deny",
        ]
    )
    return rules


def build_ip6tables_rules(bridge_name: str = SANDBOX_BRIDGE_NAME) -> list[list[str]]:
    """Build the IPv6 ip6tables commands. Mirrors :func:`build_iptables_rules`
    for IPv6. Includes the explicit ``::ffff:169.254.169.254/128`` IMDS block
    in case the broader ``::ffff:0:0/96`` rule is somehow elided."""
    rules: list[list[str]] = [
        [
            "ip6tables",
            "-I",
            "DOCKER-USER",
            "1",
            "-i",
            bridge_name,
            "-m",
            "conntrack",
            "--ctstate",
            "ESTABLISHED,RELATED",
            "-j",
            "ACCEPT",
            "-m",
            "comment",
            "--comment",
            "persona-sandbox: return traffic",
        ],
    ]
    rules.extend(
        [
            "ip6tables",
            "-A",
            "DOCKER-USER",
            "-i",
            bridge_name,
            "-d",
            cidr,
            "-j",
            "DROP",
            "-m",
            "comment",
            "--comment",
            f"persona-sandbox: block {cidr}",
        ]
        for cidr in BLOCKED_IPV6
    )
    # Explicit IPv4-mapped IMDS — belt + braces alongside the ``::ffff:0:0/96``
    # range. If a future op removes the broader range by accident, this
    # narrower one still catches the IMDS exfil attack.
    rules.append(
        [
            "ip6tables",
            "-A",
            "DOCKER-USER",
            "-i",
            bridge_name,
            "-d",
            "::ffff:169.254.169.254/128",
            "-j",
            "DROP",
            "-m",
            "comment",
            "--comment",
            "persona-sandbox: explicit IMDS via v4-mapped",
        ]
    )
    rules.append(
        [
            "ip6tables",
            "-A",
            "DOCKER-USER",
            "-i",
            bridge_name,
            "-j",
            "DROP",
            "-m",
            "comment",
            "--comment",
            "persona-sandbox: default deny",
        ]
    )
    return rules


def apply_egress_rules(
    bridge_name: str = SANDBOX_BRIDGE_NAME,
    *,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[bytes]] | None = None,
) -> None:
    """Apply the v4 + v6 rules at host setup. Requires root + Docker.

    Production deployment runs this once during host initialisation (e.g.,
    from systemd via ``persona-sandbox-egress.service``). The rules are
    idempotent at the iptables level — repeated runs add duplicate rules,
    so production should either ``-D`` the old rules first or use
    ``iptables-restore`` from a generated file.

    Args:
        bridge_name: The custom Docker bridge name.
        runner: Subprocess runner override for tests. Defaults to
            :func:`subprocess.run` with ``check=True``.

    Raises:
        SandboxUnavailableError: If any iptables command fails (likely:
            not root, or iptables/ip6tables missing).
    """
    if runner is None:

        def _default_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(  # noqa: S603 — argv list, never shell=True
                list(argv), check=True, capture_output=True
            )

        runner = _default_runner

    rules = [*build_iptables_rules(bridge_name), *build_ip6tables_rules(bridge_name)]
    for argv in rules:
        try:
            runner(argv)
        except subprocess.CalledProcessError as exc:
            _logger.warning(
                "iptables rule application failed",
                argv=" ".join(argv),
                returncode=exc.returncode,
            )
            msg = (
                f"failed to apply substrate egress rule: {' '.join(argv)}; "
                "iptables/ip6tables must be installed and the process must be root"
            )
            raise SandboxUnavailableError(
                msg,
                context={
                    "reason": "egress_rule_failed",
                    "rule": " ".join(argv),
                    "returncode": str(exc.returncode),
                },
            ) from exc
    _logger.info(
        "substrate egress filter applied",
        bridge=bridge_name,
        ipv4_rules=len(BLOCKED_IPV4),
        ipv6_rules=len(BLOCKED_IPV6),
    )
