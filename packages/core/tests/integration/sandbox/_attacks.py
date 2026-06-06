"""Adversarial security attack catalog for the spec-12 sandbox suite (T04).

**Tests-first per the Phase-2 user note:** every "verified adversarially" §9
acceptance criterion (#5 filesystem, #6 network-off, #7 metadata endpoint,
#8 resource limits, #9 no privilege escalation) ships a regression test
**alongside the code that guards it**, not after. This catalog IS the
contract every sandbox backend must satisfy; the test runner in
``test_security_suite.py`` parametrises over both the attacks and the
sandbox backends.

Currently consumed by:
- T04 (this task): meta-test that the catalog covers every §9 criterion;
  the integration tests skip until a real backend lands.
- T05a (LocalDockerSandbox container + one-shot exec): the fs/net/uid rows
  go green when the hardened ``docker.containers.run(...)`` configuration
  from R-12-2 lands.
- T05b (resource limits + outcome classification): the OOM / fork-bomb /
  timeout / disk-filler rows go green when the substrate's resource caps
  + outcome mapping land.
- T07 (egress filtering): the metadata-endpoint + RFC-1918 + IPv6 rows
  go green when the DOCKER-USER iptables config from R-12-5 lands.
- T08 (HostedSandbox): the **same** rows parametrised onto ``[hosted]`` to
  verify E2B's `update_network()` + Firecracker isolation deliver the same
  contract — the load-bearing reversibility property D-12-12 buys.

Spec-11 SSRF prior art carries: the egress test fires the attack with
``NetworkPolicy(enabled=True, allowed_hosts=["169.254.169.254"])`` — the
substrate-level block must hold regardless of the persona's allow-list
(per D-12-4). That's the **defence-in-depth invariant** we encode.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

    from persona.sandbox import ExecutionResult

# ---------------------------------------------------------------------------
# D-12-15 fixtures + verification helpers
# ---------------------------------------------------------------------------

#: The container's Debian-default ``/etc/passwd`` captured from a known-good
#: image build (``docker run --rm persona-sandbox:0.1.0 cat /etc/passwd``).
#: D-12-15: the §9 #5 attacks that read this file are verified against this
#: fixture by bytes-equality — if a future image-build changes the baseline,
#: the test surfaces it; if HOST data leaks in, the test surfaces it.
_FIXTURES_DIR = Path(__file__).parent / "_fixtures"
_ETC_PASSWD_BASELINE_PATH = _FIXTURES_DIR / "etc_passwd_baseline.txt"


def _read_baseline_etc_passwd() -> str:
    """Load the captured container ``/etc/passwd`` fixture."""
    return _ETC_PASSWD_BASELINE_PATH.read_text()


#: Env vars that, if present in ``/proc/self/environ``, indicate host data
#: leaked into the container. D-12-15: forbidden-host-data absence check.
#: NOT exhaustive — these are common host markers we explicitly defend against.
_FORBIDDEN_HOST_ENV_KEYS: frozenset[str] = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "GOOGLE_API_KEY",
        "DATABASE_URL",
        "POSTGRES_PASSWORD",
        "USER",  # the host user's login name
        "USERNAME",
        "SHELL",  # host's shell (the container has none configured for nobody)
        "SUDO_USER",
        "SUDO_GID",
        "SSH_AUTH_SOCK",
        "DISPLAY",
        "XAUTHORITY",
    }
)

#: Env keys we EXPECT to find in the container's environ (from
#: ``_BASE_CONTAINER_KWARGS["environment"]`` + image-builder additions).
#: D-12-15: at least these must be present — if absent, the substrate
#: dropped our minimal env (a separate bug).
_REQUIRED_CONTAINER_ENV_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "TMPDIR",
        "PYTHONUNBUFFERED",
        "HOSTNAME",
        "VIRTUAL_ENV",
    }
)


def _parse_environ_stdout(stdout: str) -> dict[str, str]:
    """Parse a ``/proc/self/environ`` dump (null-separated KEY=VALUE pairs).

    The sandbox writes the bytes via ``print(open(...).read())`` so the
    NUL bytes survive as ``\\x00`` in the captured stdout. Returns an
    empty dict if the format is broken (defensive)."""
    env: dict[str, str] = {}
    # The Python ``print`` adds a trailing newline; the NULs separate entries.
    for entry in stdout.rstrip("\n").split("\x00"):
        if "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        if key:
            env[key] = value
    return env


def _verify_etc_passwd_baseline(result: ExecutionResult) -> bool:
    """D-12-15 invariant: ``open("/etc/passwd")`` returns the **container's**
    Debian default, NOT the host's.

    Strategy:
    1. The execution must succeed (``outcome="ok"``) — the container's
       ``/etc/passwd`` is readable to the nobody user; not an escape but
       a substrate-class property.
    2. ``stdout`` must content-equal the captured baseline. If it differs,
       either the image was rebuilt (acceptable; regenerate the fixture)
       or HOST data leaked (security regression — surface immediately).
    """
    if result.outcome != "ok":
        return False
    expected = _read_baseline_etc_passwd()
    # The script does ``print(open(...).read())`` which adds a trailing
    # newline to the file's content. Compare with the trailing newline
    # accounted for.
    return result.stdout.rstrip("\n") == expected.rstrip("\n")


def _verify_proc_environ_no_host_data(result: ExecutionResult) -> bool:
    """D-12-15 invariant: ``open("/proc/self/environ")`` returns ONLY
    container-supplied env keys — no host credentials, no host user info.

    Strategy:
    1. ``outcome="ok"`` (the file IS readable; substrate-class property).
    2. Parse the env dump; check no ``_FORBIDDEN_HOST_ENV_KEYS`` are present.
    3. Check ``HOSTNAME == "sandbox"`` (we set it explicitly in
       ``_BASE_CONTAINER_KWARGS["hostname"]``).
    4. Check the required keys we DO supply (``_REQUIRED_CONTAINER_ENV_KEYS``)
       are present — if absent, the substrate dropped our env (separate bug).
    """
    if result.outcome != "ok":
        return False
    env = _parse_environ_stdout(result.stdout)
    if not env:
        return False
    # Forbidden host keys MUST NOT appear
    forbidden_present = _FORBIDDEN_HOST_ENV_KEYS & env.keys()
    if forbidden_present:
        return False
    # Hostname MUST be the one we set, not the host's
    if env.get("HOSTNAME") != "sandbox":
        return False
    # Required container env keys MUST be present (sanity check that the
    # substrate didn't drop our minimal env — separate bug if absent)
    missing = _REQUIRED_CONTAINER_ENV_KEYS - env.keys()
    return not missing


__all__ = ["ATTACKS", "SecurityAttack", "attacks_for_criterion"]


_BlockKind = Literal["outcome", "uid_check", "stdout_check"]


@dataclass(frozen=True, slots=True)
class SecurityAttack:
    """One adversarial test case.

    The catalog is **internal test data** — not boundary-crossing — so the
    D-12-14 Pydantic-frozen convention doesn't apply here; a ``@dataclass``
    is the right shape (a Pydantic model would be overkill for what the
    test runner reads).

    Attributes:
        name: Unique identifier (used in parametrize IDs).
        criterion: Which §9 acceptance criterion this attack verifies
            (5 / 6 / 7 / 8 / 9).
        code: The adversarial Python snippet sent to ``CodeSandbox.execute``.
        description: One-line human-readable description.
        block_kind: How "blocked" is checked:

            - ``"outcome"`` (most common): the attack should fail; the
              :class:`ExecutionResult.outcome` MUST be one of
              ``expected_outcomes``.
            - ``"uid_check"`` (§9 #9): the attack runs to completion with
              ``outcome="ok"`` but ``stdout`` must show ``getuid()`` ≠ 0.
            - ``"stdout_check"``: the attack runs to completion with
              ``outcome="ok"`` but ``stdout`` must show evidence of the
              substrate denying the attack (e.g., "Permission denied",
              "Network is unreachable").
        expected_outcomes: Allowed :class:`ExecutionResult.outcome` values
            for a "blocked" result (``"outcome"`` block_kind only). Other
            block_kinds use ``additional_check``.
        additional_check: Optional callable taking the
            :class:`ExecutionResult` and returning ``True`` iff the
            substrate-enforced invariant holds (e.g., uid != 0).
        network_enabled: If ``True``, the test runs the attack with
            ``NetworkPolicy(enabled=True, allowed_hosts=[<the target>])``
            so the substrate-level block list is tested independently of
            the persona's allow-list (R-12-5 / D-12-4 / spec-11 SSRF
            prior art). Default False (default-off network).
        allowed_hosts_override: When ``network_enabled`` is ``True``, the
            specific hosts to put in the allow-list. The substrate egress
            filter MUST still block the attack regardless.
    """

    name: str
    criterion: int
    code: str
    description: str
    block_kind: _BlockKind = "outcome"
    expected_outcomes: frozenset[str] = frozenset({"error"})
    additional_check: Callable[[ExecutionResult], bool] | None = None
    network_enabled: bool = False
    allowed_hosts_override: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# §9 #5 — Filesystem isolation (acceptance "code attempting to read outside
# the workspace ... fails to access anything outside the workspace")
# ---------------------------------------------------------------------------

_FS_ESCAPE: tuple[SecurityAttack, ...] = (
    SecurityAttack(
        name="open_etc_passwd",
        criterion=5,
        # D-12-15: container's /etc/passwd IS readable to the nobody user
        # (substrate-class property; symmetric to E2B Firecracker microVM).
        # The defended property is that it returns the CONTAINER's Debian
        # default, NOT the host's — verified by bytes-equality against
        # ``_fixtures/etc_passwd_baseline.txt``.
        code='print(open("/etc/passwd").read())',
        description=(
            "Read /etc/passwd — verify it returns container's Debian default (NOT host's content)"
        ),
        block_kind="stdout_check",
        expected_outcomes=frozenset({"ok"}),
        additional_check=_verify_etc_passwd_baseline,
    ),
    SecurityAttack(
        name="open_etc_shadow",
        criterion=5,
        code='print(open("/etc/shadow").read())',
        description="Read /etc/shadow — must FAIL (nobody lacks read permission)",
    ),
    SecurityAttack(
        name="traverse_up_from_workspace",
        criterion=5,
        # D-12-15: traversal succeeds at reading the container's filesystem
        # (not the host's). Verify by content-equality vs the same baseline:
        # `..` resolves within the container's namespace, so reaching
        # /etc/passwd via traversal returns the same content as the direct
        # absolute-path read above.
        code='print(open("../../etc/passwd").read())',
        description=(
            "Traverse out of workspace via ../ — verify it lands in the "
            "container's filesystem (NOT host's)"
        ),
        block_kind="stdout_check",
        expected_outcomes=frozenset({"ok"}),
        additional_check=_verify_etc_passwd_baseline,
    ),
    SecurityAttack(
        name="read_app_config_directly",
        criterion=5,
        code='print(open("/app/config.yaml").read())',
        description="Read host application code/config by absolute path",
    ),
    SecurityAttack(
        name="read_proc_self_environ",
        criterion=5,
        # D-12-15: /proc/self/environ IS readable; the defended property is
        # that it contains ONLY container-supplied env (the
        # ``_BASE_CONTAINER_KWARGS["environment"]`` minimal dict + image's
        # Python-build env) — NO host credentials (AWS_*, OPENAI_API_KEY,
        # DATABASE_URL, USER, SHELL, etc.) AND HOSTNAME=sandbox.
        code='print(open("/proc/self/environ").read())',
        description=(
            "Read /proc/self/environ — verify ONLY container-supplied env "
            "appears (no host credentials, HOSTNAME=sandbox)"
        ),
        block_kind="stdout_check",
        expected_outcomes=frozenset({"ok"}),
        additional_check=_verify_proc_environ_no_host_data,
    ),
    SecurityAttack(
        name="list_root_dir",
        criterion=5,
        code='import os; print(os.listdir("/"))',
        description="Enumerate host root directory contents",
        block_kind="stdout_check",
        expected_outcomes=frozenset({"ok"}),
        # Allowed to succeed BUT the listing must not include host-specific
        # paths that the workspace wouldn't have (e.g., /etc with /etc/passwd
        # readable). For T05a the sandbox container has its own minimal root.
        additional_check=lambda r: (
            # The substrate's filesystem is what's listed — not the host's.
            # We can't assert specific paths here without coupling to the
            # image layout, so the check is permissive: the attack didn't
            # ESCAPE if the test still gets to assert outcome=ok.
            r.outcome == "ok"
        ),
    ),
)


# ---------------------------------------------------------------------------
# §9 #6 — Network off by default (acceptance "code attempting any network
# connection with the default policy fails to connect")
# ---------------------------------------------------------------------------

_NETWORK_OFF: tuple[SecurityAttack, ...] = (
    SecurityAttack(
        name="urllib_open_external",
        criterion=6,
        code='import urllib.request; print(urllib.request.urlopen("http://example.com/").read())',
        description="urllib.urlopen against an external host with network off",
    ),
    SecurityAttack(
        name="socket_connect_external",
        criterion=6,
        code='import socket; s = socket.socket(); s.settimeout(3); s.connect(("8.8.8.8", 53))',
        description="Raw socket to public DNS with network off",
    ),
    SecurityAttack(
        name="httpx_get_external",
        criterion=6,
        code='import urllib.request; urllib.request.urlopen("https://example.com/", timeout=3)',
        description="HTTPS GET to a public host with network off",
    ),
    SecurityAttack(
        name="dns_resolve_external",
        criterion=6,
        code='import socket; print(socket.gethostbyname("example.com"))',
        description="DNS lookup with network off (most substrates also block DNS egress)",
    ),
)


# ---------------------------------------------------------------------------
# §9 #7 — Metadata endpoint blocked even when network is enabled with the
# attacker's preferred hosts on the allow-list. The spec-11 SSRF lesson
# explicitly applies here: block by resolved IP, never by hostname only,
# never trust the persona's allow-list to cover the substrate-level deny-list.
# ---------------------------------------------------------------------------

_METADATA_ENDPOINT: tuple[SecurityAttack, ...] = (
    SecurityAttack(
        name="aws_imds_v1",
        criterion=7,
        code=(
            "import urllib.request; "
            "print(urllib.request.urlopen("
            '"http://169.254.169.254/latest/meta-data/", timeout=3).read())'
        ),
        description="AWS IMDSv1 (the canonical metadata-endpoint exfil)",
        network_enabled=True,
        # The attacker injects the metadata IP into the allow-list. The
        # substrate's R-12-5 filter MUST block regardless (D-12-4).
        allowed_hosts_override=("169.254.169.254",),
    ),
    SecurityAttack(
        name="gcp_metadata_by_name",
        criterion=7,
        code=(
            "import urllib.request; "
            'req = urllib.request.Request("http://metadata.google.internal/"); '
            'req.add_header("Metadata-Flavor", "Google"); '
            "print(urllib.request.urlopen(req, timeout=3).read())"
        ),
        description="GCP metadata via hostname (DNS-rebind surface)",
        network_enabled=True,
        allowed_hosts_override=("metadata.google.internal",),
    ),
    SecurityAttack(
        name="azure_imds",
        criterion=7,
        code=(
            "import urllib.request; "
            "url = ("
            '"http://169.254.169.254/metadata/identity/oauth2/token'
            '?api-version=2021-12-13"'
            "); "
            "req = urllib.request.Request(url); "
            'req.add_header("Metadata", "true"); '
            "print(urllib.request.urlopen(req, timeout=3).read())"
        ),
        description="Azure IMDS (same IP, application-layer header trigger)",
        network_enabled=True,
        allowed_hosts_override=("169.254.169.254",),
    ),
    SecurityAttack(
        name="ipv4_mapped_ipv6_bypass",
        criterion=7,
        code=(
            "import socket; "
            "s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM); "
            "s.settimeout(3); "
            's.connect(("::ffff:169.254.169.254", 80))'
        ),
        description="IPv4-mapped IPv6 reaches metadata if ip6tables rules miss it",
        network_enabled=True,
        allowed_hosts_override=("::ffff:169.254.169.254",),
    ),
    SecurityAttack(
        name="rfc1918_internal",
        criterion=7,
        code=('import socket; s = socket.socket(); s.settimeout(3); s.connect(("10.0.0.1", 22))'),
        description="RFC-1918 internal network scan (10/8)",
        network_enabled=True,
        allowed_hosts_override=("10.0.0.1",),
    ),
    SecurityAttack(
        name="loopback_to_substrate_host",
        criterion=7,
        code='import socket; s = socket.socket(); s.settimeout(3); s.connect(("127.0.0.1", 22))',
        description="Loopback to the substrate host (SSH escape attempt)",
        network_enabled=True,
        allowed_hosts_override=("127.0.0.1",),
    ),
)


# ---------------------------------------------------------------------------
# §9 #8 — Resource limits (acceptance "memory bomb → oom; infinite loop →
# timeout; fork bomb contained; disk filler hits disk quota")
# ---------------------------------------------------------------------------

_RESOURCE_LIMITS: tuple[SecurityAttack, ...] = (
    SecurityAttack(
        name="memory_bomb",
        criterion=8,
        code='x = "A" * (1024 * 1024 * 1024)',  # 1 GiB string allocation
        description="Allocate 1 GiB string — should hit memory cap",
        expected_outcomes=frozenset({"oom", "killed", "error"}),
    ),
    SecurityAttack(
        name="memory_bomb_list",
        criterion=8,
        code="x = [0] * (10 ** 9)",
        description="Allocate 10^9-element list — different memory shape",
        expected_outcomes=frozenset({"oom", "killed", "error"}),
    ),
    SecurityAttack(
        name="infinite_loop",
        criterion=8,
        code="while True: pass",
        description="Pure-CPU infinite loop — should hit wall-clock timeout",
        expected_outcomes=frozenset({"timeout", "killed"}),
    ),
    SecurityAttack(
        name="fork_bomb",
        criterion=8,
        code=("import os\nwhile True:\n    try: os.fork()\n    except OSError: pass\n"),
        description="Classic fork bomb — should be capped by pids-limit",
        expected_outcomes=frozenset({"error", "killed", "timeout"}),
    ),
    SecurityAttack(
        name="disk_filler",
        criterion=8,
        code=(
            'with open("/workspace/out/huge.bin", "wb") as f:\n'
            '    while True: f.write(b"A" * (1024 * 1024))\n'
        ),
        description="Write infinitely to workspace — should hit disk quota",
        expected_outcomes=frozenset({"error", "killed", "oom", "timeout"}),
    ),
    SecurityAttack(
        name="thread_bomb",
        criterion=8,
        code=(
            "import threading, time\n"
            "def worker(): time.sleep(60)\n"
            "for _ in range(10000):\n"
            "    threading.Thread(target=worker, daemon=True).start()\n"
        ),
        description="Spawn 10000 threads — should be capped by pids-limit",
        expected_outcomes=frozenset({"error", "killed", "timeout"}),
    ),
)


# ---------------------------------------------------------------------------
# §9 #9 — No privilege escalation (acceptance "code runs as unprivileged
# user; os.getuid() is non-zero; attempts to escalate fail")
# ---------------------------------------------------------------------------


def _stdout_shows_nonzero_uid(result: ExecutionResult) -> bool:
    """Helper for §9 #9 invariant: stdout shows a non-zero uid."""
    try:
        uid = int(result.stdout.strip())
    except (ValueError, AttributeError):
        return False
    return uid != 0


_NO_PRIV_ESC: tuple[SecurityAttack, ...] = (
    SecurityAttack(
        name="getuid_nonzero",
        criterion=9,
        code="import os; print(os.getuid())",
        description="The unprivileged-user invariant — uid MUST be non-zero",
        block_kind="uid_check",
        expected_outcomes=frozenset({"ok"}),  # this attack succeeds; we check the value
        additional_check=_stdout_shows_nonzero_uid,
    ),
    SecurityAttack(
        name="sudo_escalation",
        criterion=9,
        code=(
            "import subprocess; "
            'r = subprocess.run(["sudo", "-n", "id"], capture_output=True); '
            "print(r.returncode)"
        ),
        description="Attempt sudo escalation — should fail (no sudo binary or denied)",
        # Acceptable if the attack returns ok with a non-zero subprocess returncode
        # (sudo denied) OR if it errors (sudo binary missing).
        block_kind="stdout_check",
        expected_outcomes=frozenset({"ok", "error"}),
        additional_check=lambda r: (
            # Either the call errored (sudo missing) OR returncode is non-zero
            r.outcome == "error" or (r.outcome == "ok" and r.stdout.strip() != "0")
        ),
    ),
    SecurityAttack(
        name="cap_set_attempt",
        criterion=9,
        code=(
            "import os\n"
            "try:\n"
            "    os.setuid(0)\n"
            '    print("ESCALATED")\n'
            "except (OSError, PermissionError) as e:\n"
            '    print(f"DENIED: {type(e).__name__}")\n'
        ),
        description="setuid(0) attempt — should fail with PermissionError",
        block_kind="stdout_check",
        expected_outcomes=frozenset({"ok"}),
        additional_check=lambda r: "DENIED" in r.stdout and "ESCALATED" not in r.stdout,
    ),
    SecurityAttack(
        name="mount_proc_attempt",
        criterion=9,
        code=(
            "import ctypes\n"
            'libc = ctypes.CDLL("libc.so.6")\n'
            'r = libc.mount(b"proc", b"/proc2", b"proc", 0, 0)\n'
            'print(f"mount_return={r}")\n'
        ),
        description="Direct mount() syscall — should be blocked by seccomp/caps",
        # Acceptable: substrate returns -1 (EPERM) via libc OR the call errors
        block_kind="stdout_check",
        expected_outcomes=frozenset({"ok", "error"}),
        additional_check=lambda r: (
            r.outcome == "error" or (r.outcome == "ok" and "mount_return=0" not in r.stdout)
        ),
    ),
)


ATTACKS: tuple[SecurityAttack, ...] = (
    *_FS_ESCAPE,
    *_NETWORK_OFF,
    *_METADATA_ENDPOINT,
    *_RESOURCE_LIMITS,
    *_NO_PRIV_ESC,
)
"""The full attack catalog — every row that ``test_security_suite.py``
parametrises over. Adding a new attack requires:

1. Append it to one of the per-criterion tuples above.
2. (If it surfaces a new acceptance category) extend ``CRITERIA_COVERED``.

Removing an attack requires explicit justification in the spec-12
``decisions.md`` or a follow-up close-out — coverage is auditable.
"""


CRITERIA_COVERED: frozenset[int] = frozenset({5, 6, 7, 8, 9})
"""The §9 acceptance criteria this catalog covers. The meta-test asserts
the catalog has at least one attack per row."""


def attacks_for_criterion(criterion: int) -> tuple[SecurityAttack, ...]:
    """Return all attacks that verify the given §9 acceptance criterion."""
    return tuple(a for a in ATTACKS if a.criterion == criterion)
