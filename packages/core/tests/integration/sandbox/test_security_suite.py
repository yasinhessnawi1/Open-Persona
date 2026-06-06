"""Adversarial security suite for spec-12 sandbox backends (T04).

Parametrises over the attack catalog (``_attacks.py``) AND over backend
kinds (``conftest.py``). For T04 the backends are skipped (no real
implementation exists yet); the suite goes green progressively as T05a/b
(LocalDockerSandbox) and T07 (egress filtering) land, then again on
``[hosted]`` when T08 lands.

The meta-test that the catalog covers every §9 criterion runs UNCONDITIONALLY
— that's T04's green deliverable, the contract itself; the per-backend
adversarial executions ship marked ``@pytest.mark.integration`` and skip
by default (the ``pyproject.toml`` ``addopts`` selects ``-m 'not integration
and not external'``).

T03 truncation-marker discipline carries: the suite's "truncated audit code
is recognisably truncated" assertion (the user's Phase-4 D-12-8 note) lives
in :class:`TestAuditTruncationDiscipline` and runs unconditionally — it
tests the T03 tool factory's marker contract, not a real sandbox dispatch.
"""

from __future__ import annotations

import pytest
from persona.sandbox import (
    NetworkPolicy,
    ResourceLimits,
    make_code_execution_tool,
)
from persona.sandbox.tool import TRUNCATION_MARKER_PREFIX
from persona.tools import MemoryToolAuditLogger

from ._attacks import ATTACKS, CRITERIA_COVERED, SecurityAttack, attacks_for_criterion

# ---------------------------------------------------------------------------
# Meta-tests — run unconditionally, T04's green deliverable
# ---------------------------------------------------------------------------


class TestCatalogCoverage:
    """The attack catalog covers every §9 acceptance criterion in the spec.

    These tests run on every default pytest invocation — they pin the
    contract regardless of whether a real sandbox backend is wired."""

    @pytest.mark.parametrize("criterion", sorted(CRITERIA_COVERED))
    def test_every_criterion_has_at_least_one_attack(self, criterion: int) -> None:
        """Every acceptance criterion ships ≥1 attack."""
        attacks = attacks_for_criterion(criterion)
        assert len(attacks) >= 1, (
            f"§9 acceptance #{criterion} has no attacks in the catalog — "
            "T05a/b/T07 cannot turn the missing row green if no test exists"
        )

    def test_all_attacks_have_unique_names(self) -> None:
        """Parametrize IDs are pulled from names; collisions break test reporting."""
        names = [a.name for a in ATTACKS]
        assert len(names) == len(set(names)), (
            f"Duplicate attack names in catalog: {[n for n in names if names.count(n) > 1]}"
        )

    def test_every_attack_has_a_criterion_in_covered_set(self) -> None:
        """No attack references an unknown criterion (typo defence)."""
        for attack in ATTACKS:
            assert attack.criterion in CRITERIA_COVERED, (
                f"{attack.name} references criterion {attack.criterion} "
                f"which is not in CRITERIA_COVERED={sorted(CRITERIA_COVERED)}"
            )

    def test_metadata_endpoint_attacks_enable_network(self) -> None:
        """§9 #7 invariant: the metadata-endpoint block fires REGARDLESS of
        the persona's allow-list. Every #7 attack runs with
        ``network_enabled=True`` and the target IP/host in the allow-list
        so we test the substrate-level block, not the allow-list."""
        for attack in attacks_for_criterion(7):
            assert attack.network_enabled, (
                f"{attack.name}: §9 #7 attack must run with network enabled "
                "and the metadata host in the allow-list to test the "
                "substrate-level block (D-12-4 / R-12-5)"
            )
            assert len(attack.allowed_hosts_override) > 0, (
                f"{attack.name}: §9 #7 must specify allowed_hosts_override "
                "so the substrate block list is tested independently"
            )

    def test_network_off_attacks_do_not_enable_network(self) -> None:
        """§9 #6 invariant: network is off by default — the attack runs
        without the persona's allow-list enabling network."""
        for attack in attacks_for_criterion(6):
            assert not attack.network_enabled, (
                f"{attack.name}: §9 #6 attack must NOT enable network (default-off)"
            )

    def test_summary_counts(self) -> None:
        """A summary the close-out reads — confirms the spec-§9 acceptance
        cluster has reasonable coverage. Numbers are minimums, not maximums."""
        counts = {c: len(attacks_for_criterion(c)) for c in sorted(CRITERIA_COVERED)}
        # Pin the minimums: filesystem 5, network 4, metadata 6, limits 6, privesc 4.
        # Adjust if the catalog grows.
        assert counts[5] >= 4, f"§9 #5 filesystem coverage thin: {counts[5]}"
        assert counts[6] >= 3, f"§9 #6 network-off coverage thin: {counts[6]}"
        assert counts[7] >= 5, f"§9 #7 metadata-endpoint coverage thin: {counts[7]}"
        assert counts[8] >= 5, f"§9 #8 resource-limits coverage thin: {counts[8]}"
        assert counts[9] >= 4, f"§9 #9 priv-esc coverage thin: {counts[9]}"


# ---------------------------------------------------------------------------
# T03 truncation-marker contract — the user's D-12-8 note ("T08 of the
# security suite should include a 'truncated audit code is recognisably
# truncated' assertion"). Lives here so the security suite owns the
# contract for downstream consumers.
# ---------------------------------------------------------------------------


class TestAuditTruncationDiscipline:
    """Pinning the audit-log truncation contract: a downstream consumer
    (forensic replay, automated alerting, the soak harness) MUST be able to
    detect truncated-code unambiguously without parsing ambiguity."""

    @pytest.mark.asyncio
    async def test_truncated_audit_code_is_recognisably_truncated(self) -> None:
        """The user's D-12-8 Phase-4 note: a downstream consumer must not
        mistake truncated-code for full-code. Asserts that the audit-log
        entry has:

        1. ``code_truncated == "True"`` (a discriminator flag).
        2. The literal :data:`TRUNCATION_MARKER_PREFIX` ``[truncated:`` in
           ``code`` (so even consumers that ignore ``code_truncated`` can
           detect truncation by substring).
        3. ``code_sha256`` of the **original** (untruncated) code — so a
           consumer that wants the full record can fetch it from a content-
           addressed blob store (v0.2; D-12-8 reversibility note)."""
        from hashlib import sha256

        from tests._sandbox_fakes import FakeSandbox

        big_code = "# adversarial padding\n" * 1500  # ~30 KiB
        sandbox = FakeSandbox()
        logger = MemoryToolAuditLogger()
        tool = make_code_execution_tool(
            sandbox,
            audit_logger=logger,
            persona_id="security-suite",
        )
        await tool.execute(code=big_code)

        assert len(logger.events) == 1
        event = logger.events[0]
        # (1) The discriminator flag is unambiguously set.
        assert event.metadata["code_truncated"] == "True"
        # (2) The literal marker prefix is recognisable in `code`.
        assert TRUNCATION_MARKER_PREFIX in event.metadata["code"]
        assert "bytes omitted" in event.metadata["code"]
        # (3) The sha256 references the ORIGINAL untruncated code so
        # forensic recovery is full-fidelity.
        assert event.metadata["code_sha256"] == sha256(big_code.encode()).hexdigest()
        # (4) The marker prefix appears NOWHERE in the original code so a
        # consumer can't be confused into thinking a literal "[truncated:" in
        # user-supplied code came from the truncation machinery.
        assert TRUNCATION_MARKER_PREFIX not in big_code

    @pytest.mark.asyncio
    async def test_untruncated_audit_code_has_no_marker(self) -> None:
        """Absence-of-marker is the discriminator for un-truncated code."""
        from tests._sandbox_fakes import FakeSandbox

        small_code = "print(1+1)"
        sandbox = FakeSandbox()
        logger = MemoryToolAuditLogger()
        tool = make_code_execution_tool(sandbox, audit_logger=logger)
        await tool.execute(code=small_code)

        assert len(logger.events) == 1
        event = logger.events[0]
        assert event.metadata["code_truncated"] == "False"
        assert TRUNCATION_MARKER_PREFIX not in event.metadata["code"]
        assert event.metadata["code"] == small_code


# ---------------------------------------------------------------------------
# Adversarial executions — parametrize over (sandbox, attack)
# Marked @pytest.mark.integration ⇒ skipped by default pyproject addopts.
# Skipped per-backend by the conftest fixture until T05a / T08 land.
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAdversarialSuite:
    """The actual adversarial executions against a real :class:`CodeSandbox`.

    Each test row is parametrised over ``(backend, attack)``:

    - Backend dimension comes from the conftest ``sandbox`` fixture's
      params (``"local_docker"`` / ``"hosted"``).
    - Attack dimension comes from the ``_attacks.ATTACKS`` catalog.

    For T04 every cell is skipped (no backend wired). For T05a, T05b, T07,
    T08 the matching cells turn green progressively.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "attack",
        ATTACKS,
        ids=[a.name for a in ATTACKS],
    )
    async def test_attack_is_contained(
        self,
        sandbox: object,  # CodeSandbox at runtime; object here so import doesn't fail before T05
        attack: SecurityAttack,
    ) -> None:
        # §9 #7 platform check: network-enabled attacks need the
        # ``SANDBOX_BRIDGE_NAME`` Docker bridge with the R-12-5 egress filter
        # applied via ``setup-sandbox-net.sh``. macOS dev hosts cannot
        # empirically verify §9 #7 (iptables is Linux-only); on a Linux host
        # without the setup script run, the bridge is also absent. Either way
        # we skip cleanly with a precise pointer message — NOT a false-positive
        # failure that would obscure real regressions.
        if attack.network_enabled:
            import docker as _docker_sdk

            try:
                _client = _docker_sdk.from_env()
                try:
                    from persona.sandbox.egress import SANDBOX_BRIDGE_NAME

                    _client.networks.get(SANDBOX_BRIDGE_NAME)
                finally:
                    _client.close()
            except _docker_sdk.errors.NotFound:
                pytest.skip(
                    "§9 #7 attack requires the substrate egress filter applied. "
                    "On Linux: sudo packages/core/src/persona/sandbox/scripts/"
                    "setup-sandbox-net.sh. On macOS: §9 #7 empirical verification "
                    "is Linux-only (iptables). 26 rule-construction unit tests "
                    "in test_egress.py pin the catalog."
                )

        """The substrate MUST contain ``attack.code`` per ``attack``'s
        block_kind invariant.

        - ``block_kind="outcome"`` — :class:`ExecutionResult.outcome` MUST
          be in ``attack.expected_outcomes``.
        - ``block_kind="uid_check"`` or ``"stdout_check"`` — the attack
          completes (``outcome="ok"``) but the substrate-enforced invariant
          (``attack.additional_check``) holds.

        Network attacks (§9 #6, #7) construct the appropriate
        :class:`NetworkPolicy` so the substrate-level block is tested
        independently of the persona's allow-list.
        """
        from persona.sandbox import CodeSandbox  # local import — Protocol at runtime

        assert isinstance(sandbox, CodeSandbox)

        network = NetworkPolicy(
            enabled=attack.network_enabled,
            allowed_hosts=attack.allowed_hosts_override,
        )
        # Tight limits so resource-limit attacks fire quickly; the substrate's
        # caps are independent of these per-call arguments (D-12-X structural
        # invariant: limits are enforced by the substrate, not by the code).
        limits = ResourceLimits(
            cpu_cores=1.0,
            memory_mb=256,
            wall_clock_s=5.0,
            disk_mb=64,
        )
        result = await sandbox.execute(
            attack.code,
            limits=limits,
            network=network,
        )

        if attack.block_kind == "outcome":
            assert result.outcome in attack.expected_outcomes, (
                f"{attack.name}: attack NOT CONTAINED — outcome was "
                f"{result.outcome!r}, expected one of "
                f"{sorted(attack.expected_outcomes)}. "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
        else:
            # uid_check / stdout_check: the attack runs to completion; the
            # substrate-enforced invariant is the check.
            assert result.outcome in attack.expected_outcomes, (
                f"{attack.name}: unexpected outcome {result.outcome!r}; "
                f"expected {sorted(attack.expected_outcomes)}"
            )
            assert attack.additional_check is not None
            assert attack.additional_check(result), (
                f"{attack.name}: substrate-enforced invariant FAILED. "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
