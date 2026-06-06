# persona-sandbox host setup scripts

Substrate-level egress filter setup for `LocalDockerSandbox` (spec 12 T07).

| File | Purpose |
|---|---|
| [`setup-sandbox-net.sh`](setup-sandbox-net.sh) | Create the Docker bridge + apply the R-12-5 iptables/ip6tables rules. **Root required.** |
| [`teardown-sandbox-net.sh`](teardown-sandbox-net.sh) | Reverse of setup. Removes the rules (matched by comment) and the bridge. **Root required.** |

## Why these scripts exist (and live HERE)

These scripts apply the spec-12 T07 substrate-level egress filter (R-12-5) on
the host where `LocalDockerSandbox` runs untrusted code. The blocked CIDR list
(`BLOCKED_IPV4`, `BLOCKED_IPV6`) is single-source-of-truth in
[`../egress.py`](../egress.py); these scripts call the Python entry point
[`apply_egress_rules`](../egress.py) to generate the rules, so the scripts and
the unit tests in [`../../../../tests/unit/sandbox/test_egress.py`](../../../../tests/unit/sandbox/test_egress.py)
can never drift.

### Location choice

The scripts live in `packages/core/src/persona/sandbox/scripts/` alongside
[`../image/`](../image/) (the substrate's Dockerfile + pinned manifest), rather
than in `packages/api/scripts/`:

- **D-12-13 threat-model separation:** the local Docker substrate is in core;
  the hosted E2B path (D-12-12) uses E2B's native `update_network()` API for
  egress filtering, NOT these scripts. The api package has no substrate setup
  of its own.
- **Co-located with the code that uses them.** `LocalDockerSandbox` references
  `SANDBOX_BRIDGE_NAME` and `apply_egress_rules` from
  [`../egress.py`](../egress.py); the scripts wrap the same entry point.
- **Future-proofs the v0.2 self-Fly Machines exit ramp** (the D-12-12 fallback
  if E2B doesn't pass the five lock-gates): the API deploy playbook will
  reference these canonical scripts via the core path.

## Platform — Linux only

**These scripts require a Linux host.** `iptables` / `ip6tables` are Linux
tools; macOS and Windows don't have them. macOS Docker Desktop runs
containers in a hidden Linux VM (LinuxKit) — that VM has iptables but it's
not directly reachable from the macOS host.

Running `setup-sandbox-net.sh` on macOS exits with a clear platform
limitation message before creating any host state.

**What this means for spec 12 §9 acceptance verification:**

- **§9 #5, #6, #8, #9 verify cleanly on macOS** — R-12-2 container hardening
  (cap_drop, seccomp, read_only, network=none, resource caps, non-root user)
  is container-level config Docker Desktop applies via its Linux VM
  regardless of host OS. **20/20 §9 #5/#6/#8/#9 attacks contained empirically
  on macOS Docker Desktop**, verified by the integration suite.
- **§9 #7 (metadata endpoint blocking) cannot be empirically verified on
  macOS dev hosts.** The 26 unit tests in
  [`../../../../tests/unit/sandbox/test_egress.py`](../../../../tests/unit/sandbox/test_egress.py)
  pin the rule construction (catalog completeness, DOCKER-USER targeting,
  IPv4-mapped-IPv6 belt-and-braces, etc.). Live verification requires a
  Linux host — production deploy or a Linux dev VM (OrbStack, multipass,
  Vagrant).

This is the macOS dev-host limitation; not a regression, not a hardening
gap. The scripts are designed for the production Linux deploy environment;
the macOS dev host is the test bed for the deploy case, not the deploy
itself.

## When to run

### Local-dev test bed (Linux contributors only)

Once after cloning the repo on a Linux host, before running the spec-12
integration security suite (`packages/core/tests/integration/sandbox/`):

```bash
sudo packages/core/src/persona/sandbox/scripts/setup-sandbox-net.sh
uv run pytest packages/core/tests/integration/sandbox/ -m integration
```

The §9 #7 metadata-endpoint attacks (`aws_imds_v1`, `gcp_metadata_by_name`,
etc.) require this setup to be in place — without it, they skip with
`network persona-sandbox-net not found`.

Re-run `setup-sandbox-net.sh` after Docker daemon restart (rules don't
persist by default).

### Hosted deploy (D-12-12 self-Fly Machines exit ramp)

Once per host at provisioning time. The hosted E2B path (the D-12-12 v0.1
default) does NOT use these scripts — E2B has its own `update_network()` API
for substrate-level egress. The scripts apply ONLY when LocalDockerSandbox is
the substrate (CLI; self-Fly v0.2 exit ramp).

## What the rules do

`setup-sandbox-net.sh` runs:

1. `docker network create persona-sandbox-net` (idempotent — skipped if present).
2. `apply_egress_rules(SANDBOX_BRIDGE_NAME)` from
   [`../egress.py`](../egress.py) — applies ~26 iptables rules + ~28 ip6tables
   rules to the `DOCKER-USER` chain. Each rule carries a
   `--comment "persona-sandbox: ..."` so teardown can match-and-remove cleanly.

The rules DROP traffic from the sandbox bridge to:

- **Cloud metadata** — `169.254.169.254` (AWS/GCP/Azure IMDS) + IPv6
  equivalents (`fd00:ec2::254` etc.)
- **RFC-1918 private ranges** — `10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`
- **IPv6 link-local + ULA** — `fe80::/10`, `fc00::/7`
- **IPv4-mapped IPv6** — `::ffff:0:0/96` (defeats v4-via-v6 bypass — see spec
  12 R-12-5)
- **Loopback** — `127.0.0.0/8`, `::1/128`
- **Multicast + broadcast** — `224.0.0.0/4`, `ff00::/8`, `255.255.255.255`
- **CGNAT + TEST-NET + documentation ranges** — RFC 6598, RFC 5737, RFC 6890

Rules apply **before** any persona's `NetworkPolicy.allowed_hosts` — the
substrate-level deny-list fires regardless of the model's allow-list (the
spec-11 SSRF prior-art discipline).

## Manual reverse-out (if a script crashes mid-apply)

```bash
# IPv4 — match by our comment prefix; delete by line number descending
sudo iptables -L DOCKER-USER -n --line-numbers | grep 'persona-sandbox'
# Then sudo iptables -D DOCKER-USER <line-number> for each match (descending).

# IPv6 — same shape
sudo ip6tables -L DOCKER-USER -n --line-numbers | grep 'persona-sandbox'

# Bridge
sudo docker network rm persona-sandbox-net
```

## Source of truth

- **Blocked CIDR list:** [`../egress.py`](../egress.py) — `BLOCKED_IPV4`,
  `BLOCKED_IPV6` constants.
- **Rule generation:** `build_iptables_rules` / `build_ip6tables_rules` in
  [`../egress.py`](../egress.py).
- **Rule application:** `apply_egress_rules` in [`../egress.py`](../egress.py),
  invoked by this script.
- **Test coverage:** [`../../../../tests/unit/sandbox/test_egress.py`](../../../../tests/unit/sandbox/test_egress.py)
  (rule construction; pinned 26-test invariants).
- **Adversarial verification:** the §9 #7 catalog in
  [`../../../../tests/integration/sandbox/_attacks.py`](../../../../tests/integration/sandbox/_attacks.py)
  exercises this filter against a real sandbox once the bridge exists.
