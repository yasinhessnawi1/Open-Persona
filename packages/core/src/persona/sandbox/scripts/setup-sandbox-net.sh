#!/usr/bin/env bash
# persona-sandbox network setup — spec 12 T07 / R-12-5.
# Applies the substrate-level egress filter on a custom Docker bridge.
# REQUIRES ROOT (sudo). See ./README.md for what this does + when to run it.
#
# SELF-CONTAINED: the CIDR list below mirrors ``BLOCKED_IPV4`` and
# ``BLOCKED_IPV6`` in ../egress.py (Python source-of-truth for the test
# suite). When egress.py adds a CIDR, this script must add it too.
# Test coverage: packages/core/tests/unit/sandbox/test_egress.py pins the
# Python catalog; manual diff against this script is the v0.1 cross-check.
# (A v0.2 follow-up could automate the diff in CI.)

set -euo pipefail

BRIDGE_NAME="${BRIDGE_NAME:-persona-sandbox-net}"

if [[ "$(id -u)" != "0" ]]; then
  echo "ERROR: setup-sandbox-net.sh must be run as root (sudo)" >&2
  echo "       iptables / ip6tables / docker network create all require it." >&2
  exit 1
fi

# Platform check — iptables is Linux-only. macOS dev hosts run Docker Desktop
# with a hidden Linux VM (LinuxKit); the VM has iptables but it's not directly
# reachable from macOS. R-12-5 substrate egress requires a Linux host (the
# production / hosted-deploy environment). The R-12-2 container hardening
# (cap_drop, seccomp, read_only, network=none, resource caps, non-root user)
# DOES work on macOS — that's container-level config Docker Desktop applies
# via its Linux VM regardless of the host OS. Only the substrate-level egress
# filter (this script) needs a Linux host.
if [[ "$(uname -s)" != "Linux" ]]; then
  echo ""
  echo "ERROR: setup-sandbox-net.sh requires a Linux host. Detected: $(uname -s)"
  echo ""
  echo "This script applies iptables/ip6tables rules to the DOCKER-USER chain."
  echo "macOS / Windows / BSD hosts do not have iptables — Docker Desktop's"
  echo "hidden Linux VM has its own iptables but it's not directly reachable"
  echo "from the host."
  echo ""
  echo "The substrate egress filter (R-12-5) is required for the §9 #7"
  echo "metadata-endpoint acceptance criterion in spec 12. Options:"
  echo ""
  echo "  1. Run this script on the production Linux deploy host (the"
  echo "     intended target — the macOS dev host is the test bed for the"
  echo "     deploy case, not the deploy itself)."
  echo "  2. Run on a Linux dev VM (OrbStack, multipass, Vagrant, etc.)."
  echo "  3. Skip §9 #7 empirical verification in macOS dev runs. The 26 unit"
  echo "     tests in packages/core/tests/unit/sandbox/test_egress.py pin the"
  echo "     rule construction; empirical verification on Linux."
  echo ""
  echo "All other §9 acceptance criteria (#5 filesystem, #6 network-off, #8"
  echo "resource limits, #9 no priv-esc) ARE verifiable on macOS — the R-12-2"
  echo "container hardening works on Docker Desktop unchanged."
  echo ""
  exit 2
fi

# ---------------------------------------------------------------------------
# Mirror of BLOCKED_IPV4 from packages/core/src/persona/sandbox/egress.py
# ---------------------------------------------------------------------------
BLOCKED_IPV4=(
  "127.0.0.0/8"      # Loopback (RFC 1122)
  "10.0.0.0/8"       # RFC 1918 private (Class A)
  "172.16.0.0/12"    # RFC 1918 private (Class B)
  "192.168.0.0/16"   # RFC 1918 private (Class C)
  "169.254.0.0/16"   # RFC 3927 link-local — includes 169.254.169.254 IMDS
  "100.64.0.0/10"    # RFC 6598 CGNAT
  "192.0.0.0/24"     # RFC 6890 IETF protocol assignments
  "192.0.2.0/24"     # RFC 6890 TEST-NET-1
  "198.51.100.0/24"  # RFC 6890 TEST-NET-2
  "203.0.113.0/24"   # RFC 6890 TEST-NET-3
  "198.18.0.0/15"    # RFC 2544 benchmarking
  "224.0.0.0/4"      # RFC 5771 multicast
  "240.0.0.0/4"      # RFC 1112 reserved (includes 255.255.255.255 broadcast)
  "0.0.0.0/8"        # RFC 1122 "this network"
)

# ---------------------------------------------------------------------------
# Mirror of BLOCKED_IPV6 from packages/core/src/persona/sandbox/egress.py
# ---------------------------------------------------------------------------
BLOCKED_IPV6=(
  "::1/128"          # Loopback (RFC 4291)
  "fc00::/7"         # Unique-local (RFC 4193 — includes AWS IPv6 IMDS)
  "fe80::/10"        # Link-local (RFC 4291)
  "ff00::/8"         # Multicast (RFC 4291)
  "2001:db8::/32"    # Documentation (RFC 3849)
  "64:ff9b::/96"     # NAT64 (RFC 6052)
  "::ffff:0:0/96"    # IPv4-mapped (defeats v4-via-v6 bypass)
  "2002::/16"        # 6to4 (RFC 3056 — can encapsulate private v4)
)

echo "[1/3] Creating Docker bridge: $BRIDGE_NAME"
if docker network inspect "$BRIDGE_NAME" >/dev/null 2>&1; then
  echo "      (bridge already exists; skipping create)"
else
  docker network create --driver bridge "$BRIDGE_NAME" >/dev/null
  echo "      created."
fi

echo "[2/3] Applying IPv4 + IPv6 egress rules to DOCKER-USER chain"

# Conntrack ACCEPT for return traffic — MUST be at position 1 so it fires
# before any DROP rules. Without this, allow-listed outbound flows never
# receive replies.
iptables -I DOCKER-USER 1 -i "$BRIDGE_NAME" \
  -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT \
  -m comment --comment "persona-sandbox: return traffic"

ip6tables -I DOCKER-USER 1 -i "$BRIDGE_NAME" \
  -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT \
  -m comment --comment "persona-sandbox: return traffic" 2>/dev/null || \
  echo "      (warning: ip6tables conntrack rule failed — IPv6 may be disabled on this host)"

# DROP every blocked IPv4 CIDR.
for cidr in "${BLOCKED_IPV4[@]}"; do
  iptables -A DOCKER-USER -i "$BRIDGE_NAME" -d "$cidr" -j DROP \
    -m comment --comment "persona-sandbox: block $cidr"
done

# DROP every blocked IPv6 CIDR.
for cidr in "${BLOCKED_IPV6[@]}"; do
  ip6tables -A DOCKER-USER -i "$BRIDGE_NAME" -d "$cidr" -j DROP \
    -m comment --comment "persona-sandbox: block $cidr" 2>/dev/null || true
done

# Explicit IPv4-mapped IMDS belt + braces (egress.py R-12-5 fallback).
ip6tables -A DOCKER-USER -i "$BRIDGE_NAME" \
  -d "::ffff:169.254.169.254/128" -j DROP \
  -m comment --comment "persona-sandbox: explicit IMDS via v4-mapped" \
  2>/dev/null || true

# Default DROP at the tail — sandbox traffic not explicitly accepted is dropped.
# The composition root inserts the user's NetworkPolicy.allowed_hosts ACCEPT
# rules ABOVE this default DROP at runtime (per persona, per sandbox session).
iptables -A DOCKER-USER -i "$BRIDGE_NAME" -j DROP \
  -m comment --comment "persona-sandbox: default deny"

ip6tables -A DOCKER-USER -i "$BRIDGE_NAME" -j DROP \
  -m comment --comment "persona-sandbox: default deny" 2>/dev/null || true

echo "[3/3] Verifying rules"
v4_count=$(iptables -L DOCKER-USER -n -v 2>/dev/null | grep -c persona-sandbox || true)
v6_count=$(ip6tables -L DOCKER-USER -n -v 2>/dev/null | grep -c persona-sandbox || true)
echo "      $v4_count IPv4 rules tagged 'persona-sandbox' present."
echo "      $v6_count IPv6 rules tagged 'persona-sandbox' present."

echo ""
echo "Setup complete. Bridge '$BRIDGE_NAME' now drops egress to:"
echo "  - 169.254.169.254 (cloud metadata) + IPv6 equivalents"
echo "  - RFC-1918 private ranges (10/8, 172.16/12, 192.168/16)"
echo "  - IPv6 link-local (fe80::/10) + ULA (fc00::/7)"
echo "  - IPv4-mapped IPv6 ::ffff:0:0/96 (defeats v4-via-v6 bypass)"
echo "  - Loopback, multicast, broadcast, CGNAT, TEST-NET"
echo ""
echo "Rules apply BEFORE any persona's NetworkPolicy.allowed_hosts — the"
echo "substrate-level deny-list fires regardless of the model's allow-list."
echo ""
echo "Rules do NOT persist across Docker daemon restarts. Re-run on restart."
echo "Reverse out: ./teardown-sandbox-net.sh"
