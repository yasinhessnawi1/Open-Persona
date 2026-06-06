"""Unit tests for the spec-12 T07 substrate egress filter (R-12-5).

The iptables/ip6tables commands themselves require root + a Docker daemon
to apply at runtime. These tests verify the **rule construction** (every
R-12-5 CIDR present; rule ordering; chain targeting) and the
:func:`apply_egress_rules` runner contract via a mock subprocess runner —
no real iptables invocation needed.

Pinned invariants the §9 #7 acceptance depends on:
- IMDS ``169.254.169.254`` is in a blocked range (via ``169.254.0.0/16``).
- RFC-1918 ``10/8`` / ``172.16/12`` / ``192.168/16`` blocked.
- IPv6 link-local ``fe80::/10`` blocked.
- ULA ``fc00::/7`` blocked (includes AWS IPv6 IMDS).
- IPv4-mapped-IPv6 ``::ffff:0:0/96`` blocked (defeats v4-via-v6 bypass).
- Loopback ``127/8`` and ``::1`` blocked.
- All rules target the ``DOCKER-USER`` chain (Docker's stable extension point).
- Rules apply per-bridge — other Docker containers on the host unaffected.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from persona.sandbox import SandboxUnavailableError

if TYPE_CHECKING:
    from pathlib import Path
from persona.sandbox.egress import (
    BLOCKED_IPV4,
    BLOCKED_IPV6,
    SANDBOX_BRIDGE_NAME,
    apply_egress_rules,
    build_ip6tables_rules,
    build_iptables_rules,
)

# ---------------------------------------------------------------------------
# R-12-5 catalog completeness — pin the deny-list invariants
# ---------------------------------------------------------------------------


class TestBlockedRangesCompleteness:
    """The R-12-5 deny-list MUST cover every range the spec-§9 #7 attack
    catalog targets. Adding new attacks to ``_attacks.py`` without extending
    these tests would surface here."""

    def test_imds_ipv4_in_blocked_range(self) -> None:
        """169.254.169.254 must be inside 169.254.0.0/16 (which is in the list)."""
        assert "169.254.0.0/16" in BLOCKED_IPV4

    def test_rfc1918_ranges_blocked(self) -> None:
        """All three RFC 1918 private ranges are blocked."""
        for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
            assert cidr in BLOCKED_IPV4, f"RFC-1918 {cidr} missing"

    def test_loopback_v4_blocked(self) -> None:
        assert "127.0.0.0/8" in BLOCKED_IPV4

    def test_loopback_v6_blocked(self) -> None:
        assert "::1/128" in BLOCKED_IPV6

    def test_ipv6_link_local_blocked(self) -> None:
        """``fe80::/10`` — covers all IPv6 link-local; required because some
        cloud substrates expose metadata via IPv6 link-local addresses."""
        assert "fe80::/10" in BLOCKED_IPV6

    def test_ipv6_ula_blocked(self) -> None:
        """``fc00::/7`` — RFC 4193 ULA; AWS IPv6 IMDS is at fd00:ec2::254."""
        assert "fc00::/7" in BLOCKED_IPV6

    def test_ipv4_mapped_v6_blocked(self) -> None:
        """``::ffff:0:0/96`` — defeats the v4-via-v6 bypass attack:
        ``socket.AF_INET6`` to ``::ffff:169.254.169.254`` would otherwise
        route through ip6tables AND skip iptables rules."""
        assert "::ffff:0:0/96" in BLOCKED_IPV6

    def test_cgnat_blocked(self) -> None:
        """RFC 6598 CGNAT — often used by cloud LB internal hops."""
        assert "100.64.0.0/10" in BLOCKED_IPV4

    def test_multicast_blocked(self) -> None:
        assert "224.0.0.0/4" in BLOCKED_IPV4
        assert "ff00::/8" in BLOCKED_IPV6

    def test_v4_broadcast_blocked(self) -> None:
        """Broadcast ``255.255.255.255`` falls in ``240.0.0.0/4``."""
        assert "240.0.0.0/4" in BLOCKED_IPV4

    def test_documentation_and_test_net_blocked(self) -> None:
        """RFC 5737 TEST-NET addresses and RFC 6890 IETF protocol
        assignments are blocked — defence-in-depth (these shouldn't be
        routable in public anyway, but explicit denial is safe)."""
        assert "192.0.2.0/24" in BLOCKED_IPV4
        assert "2001:db8::/32" in BLOCKED_IPV6


# ---------------------------------------------------------------------------
# Rule construction — DOCKER-USER chain, proper ordering, bridge targeting
# ---------------------------------------------------------------------------


class TestIptablesRules:
    def test_rules_target_docker_user_chain(self) -> None:
        """Docker's stable extension point — promises not to flush it."""
        rules = build_iptables_rules()
        for rule in rules:
            assert "DOCKER-USER" in rule, f"rule not in DOCKER-USER: {rule}"

    def test_rules_target_sandbox_bridge_by_default(self) -> None:
        """``-i SANDBOX_BRIDGE_NAME`` filters traffic from the sandbox
        bridge only — other Docker bridges / containers on the host
        unaffected."""
        rules = build_iptables_rules()
        for rule in rules:
            assert SANDBOX_BRIDGE_NAME in rule

    def test_custom_bridge_name_supported(self) -> None:
        rules = build_iptables_rules(bridge_name="custom-net")
        for rule in rules:
            assert "custom-net" in rule
            assert SANDBOX_BRIDGE_NAME not in rule

    def test_conntrack_accept_at_position_1(self) -> None:
        """Return traffic ACCEPT MUST be at position 1 so it fires before
        the default DROP at the tail. Without this, allow-listed outbound
        flows never receive replies."""
        rules = build_iptables_rules()
        first = rules[0]
        assert "-I" in first
        # Position arg is "1"
        assert first[first.index("-I") + 2] == "1"
        assert "ESTABLISHED,RELATED" in first
        assert "ACCEPT" in first

    def test_every_blocked_ipv4_has_a_drop_rule(self) -> None:
        rules = build_iptables_rules()
        # Each CIDR appears as a -d argument in a DROP rule.
        cidrs_in_rules = {rule[rule.index("-d") + 1] for rule in rules if "-d" in rule}
        for cidr in BLOCKED_IPV4:
            assert cidr in cidrs_in_rules, f"missing DROP for {cidr}"

    def test_default_drop_at_tail(self) -> None:
        last = build_iptables_rules()[-1]
        assert "DROP" in last
        # No -d means "all destinations" — the default-drop
        assert "-d" not in last


class TestIp6tablesRules:
    def test_uses_ip6tables_binary(self) -> None:
        rules = build_ip6tables_rules()
        for rule in rules:
            assert rule[0] == "ip6tables"

    def test_every_blocked_ipv6_has_a_drop_rule(self) -> None:
        rules = build_ip6tables_rules()
        cidrs_in_rules = {rule[rule.index("-d") + 1] for rule in rules if "-d" in rule}
        for cidr in BLOCKED_IPV6:
            assert cidr in cidrs_in_rules, f"missing v6 DROP for {cidr}"

    def test_explicit_ipv4_mapped_imds_rule(self) -> None:
        """Belt + braces: even though ``::ffff:0:0/96`` covers it, we have
        an explicit ``::ffff:169.254.169.254/128`` rule — if a future op
        accidentally removes the broader range, IMDS exfil still blocked."""
        rules = build_ip6tables_rules()
        explicit_imds = [rule for rule in rules if "::ffff:169.254.169.254/128" in rule]
        assert len(explicit_imds) == 1


# ---------------------------------------------------------------------------
# apply_egress_rules — runner contract + error mapping
# ---------------------------------------------------------------------------


class TestApplyEgressRules:
    def test_runs_every_rule_through_runner(self) -> None:
        runner = MagicMock(return_value=subprocess.CompletedProcess([], 0))
        apply_egress_rules(runner=runner)
        # Both v4 and v6 rules were dispatched
        expected = len(build_iptables_rules()) + len(build_ip6tables_rules())
        assert runner.call_count == expected

    def test_failure_maps_to_sandbox_unavailable(self) -> None:
        """A non-root caller or missing iptables binary surfaces a clean
        domain exception — not a raw CalledProcessError."""

        def _failing_runner(_argv: list[str]) -> subprocess.CompletedProcess[bytes]:
            raise subprocess.CalledProcessError(returncode=1, cmd=_argv, output=b"")

        with pytest.raises(SandboxUnavailableError) as exc_info:
            apply_egress_rules(runner=_failing_runner)
        assert exc_info.value.context["reason"] == "egress_rule_failed"
        assert exc_info.value.context["returncode"] == "1"

    def test_argv_form_only_no_shell(self) -> None:
        """Every rule the runner receives is an argv list (defence against
        shell injection — the hook reminder). No string concatenation."""
        captured: list[list[str]] = []

        def _capturing_runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
            captured.append(list(argv))
            return subprocess.CompletedProcess([], 0)

        apply_egress_rules(runner=_capturing_runner)
        for argv in captured:
            assert isinstance(argv, list), "runner received non-argv input"
            assert all(isinstance(arg, str) for arg in argv), (
                "argv contains non-string element — possible injection risk"
            )


# ---------------------------------------------------------------------------
# Smoke test: LocalDockerSandbox uses SANDBOX_BRIDGE_NAME when network on
# ---------------------------------------------------------------------------


class TestLocalDockerSandboxBridgeWiring:
    """T07's load-bearing wire: ``LocalDockerSandbox`` sets
    ``network_mode=SANDBOX_BRIDGE_NAME`` when ``NetworkPolicy.enabled=True``
    so the substrate-level filter sees the traffic."""

    def test_one_shot_exec_uses_sandbox_bridge(self, tmp_path: Path) -> None:
        from persona.sandbox import NetworkPolicy, ResourceLimits
        from persona.sandbox.local_docker import LocalDockerSandbox

        client = MagicMock()
        sandbox = LocalDockerSandbox(workspace_root=tmp_path, docker_client=client)
        host_in = tmp_path / "in"
        host_out = tmp_path / "out"
        host_in.mkdir()
        host_out.mkdir()
        kwargs = sandbox._build_container_kwargs(
            host_in=host_in,
            host_out=host_out,
            limits=ResourceLimits(),
            network=NetworkPolicy(enabled=True, allowed_hosts=("example.com",)),
            exec_id="t07",
        )
        assert kwargs["network_mode"] == SANDBOX_BRIDGE_NAME

    def test_session_create_uses_sandbox_bridge(self, tmp_path: Path) -> None:
        """The session-mode path (T05c) also uses the custom bridge — so
        the R-12-5 filter covers stateful sessions too."""
        from persona.sandbox import NetworkPolicy, ResourceLimits
        from persona.sandbox.local_docker import LocalDockerSandbox

        client = MagicMock()
        # exec_run isn't called here; we just need .containers.run to return.
        container = MagicMock()
        client.containers.run.return_value = container
        sandbox = LocalDockerSandbox(workspace_root=tmp_path, docker_client=client)

        import asyncio

        asyncio.run(
            sandbox.create_session(
                "tenant-1:conv-1",
                limits=ResourceLimits(),
                network=NetworkPolicy(enabled=True, allowed_hosts=("example.com",)),
            )
        )
        run_kwargs = client.containers.run.call_args.kwargs
        assert run_kwargs["network_mode"] == SANDBOX_BRIDGE_NAME

    def test_session_create_uses_none_when_network_disabled(self, tmp_path: Path) -> None:
        from persona.sandbox import NetworkPolicy, ResourceLimits
        from persona.sandbox.local_docker import LocalDockerSandbox

        client = MagicMock()
        container = MagicMock()
        client.containers.run.return_value = container
        sandbox = LocalDockerSandbox(workspace_root=tmp_path, docker_client=client)

        import asyncio

        asyncio.run(
            sandbox.create_session(
                "tenant-1:conv-2",
                limits=ResourceLimits(),
                network=NetworkPolicy(),  # default off
            )
        )
        run_kwargs = client.containers.run.call_args.kwargs
        assert run_kwargs["network_mode"] == "none"
