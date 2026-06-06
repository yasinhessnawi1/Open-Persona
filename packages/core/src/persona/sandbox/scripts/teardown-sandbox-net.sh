#!/usr/bin/env bash
# persona-sandbox network teardown — reverse of setup-sandbox-net.sh.
# Removes iptables rules we added (matched by 'persona-sandbox' comment) and
# the custom Docker bridge. REQUIRES ROOT.

set -euo pipefail

BRIDGE_NAME="${BRIDGE_NAME:-persona-sandbox-net}"

if [[ "$(id -u)" != "0" ]]; then
  echo "ERROR: teardown-sandbox-net.sh must be run as root (sudo)" >&2
  exit 1
fi

# On macOS, iptables doesn't exist on the host (see setup-sandbox-net.sh).
# Skip the iptables steps gracefully so a teardown after a half-applied
# state (bridge created on macOS, no rules) still cleans up the bridge.
HAS_IPTABLES=1
if ! command -v iptables >/dev/null 2>&1; then
  HAS_IPTABLES=0
  echo "INFO: iptables not present (likely macOS host); skipping rule removal."
fi

if [[ "$HAS_IPTABLES" == "1" ]]; then
  echo "[1/3] Removing IPv4 rules tagged 'persona-sandbox' from DOCKER-USER"
  # Delete by line number, descending (so deletes don't shift remaining indices).
  iptables -L DOCKER-USER -n --line-numbers \
    | grep persona-sandbox \
    | awk '{print $1}' \
    | sort -rn \
    | xargs -I {} iptables -D DOCKER-USER {} 2>/dev/null || true

  echo "[2/3] Removing IPv6 rules tagged 'persona-sandbox' from DOCKER-USER"
  ip6tables -L DOCKER-USER -n --line-numbers 2>/dev/null \
    | grep persona-sandbox \
    | awk '{print $1}' \
    | sort -rn \
    | xargs -I {} ip6tables -D DOCKER-USER {} 2>/dev/null || true
else
  echo "[1/3] Skipping IPv4 rule removal (no iptables on this host)"
  echo "[2/3] Skipping IPv6 rule removal (no iptables on this host)"
fi

echo "[3/3] Removing Docker bridge: $BRIDGE_NAME"
if docker network inspect "$BRIDGE_NAME" >/dev/null 2>&1; then
  docker network rm "$BRIDGE_NAME" >/dev/null && echo "      removed."
else
  echo "      (bridge not present; nothing to remove)"
fi

echo ""
echo "Teardown complete. Run setup-sandbox-net.sh to re-apply."
