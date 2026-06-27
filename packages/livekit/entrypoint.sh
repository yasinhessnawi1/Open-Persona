#!/bin/sh
# Open Persona — LiveKit SFU entrypoint for Fly.io (app: open-persona-livekit).
#
# The Fly-specific glue the static livekit.yaml can't express (see
# docs/research/livekit_selfhost_fly.md):
#   1. Resolve the browser-reachable public IP and pass it as --node-ip, so the
#      advertised ICE candidate is the address the browser actually hits.
#   2. Exec livekit-server with the baked config.
#
# Keys: livekit-server reads the LIVEKIT_KEYS env var directly (the built-in
# --keys flag), so we do NOT write a key_file — we just assert the secret is set.
# Format: "apiKey: secret" (the SAME pair the voice service signs tokens with).
#
# Bind: --bind makes livekit-server listen on the given address (overrides the
# config's bind_addresses for ALL listeners). Fly REQUIRES UDP listeners to bind
# to `fly-global-services` (binding 0.0.0.0 makes Linux pick the wrong source
# address on UDP replies). `fly-global-services` resolves to the VM's reachable
# address, so TCP/HTTP signaling works on it too — one bind satisfies both.
# OPERATOR-PASS CHECK: if signaling (wss) fails to connect after deploy, the
# fallback is to drop --bind entirely (LiveKit defaults to 0.0.0.0 for TCP) and
# rely on --node-ip alone for the UDP candidate, the pattern the community Fly
# example used. See docs/research/livekit_selfhost_fly.md §1.3.
#
# Fail-fast: a missing precondition aborts the boot rather than coming up broken.
set -eu

CONFIG_PATH="${LIVEKIT_CONFIG_PATH:-/etc/livekit/livekit.yaml}"

# --- node IP ------------------------------------------------------------------
# Precedence: explicit LIVEKIT_NODE_IP secret (escape hatch if DNS returns the
# wrong family) > the app's own .fly.dev A record. On a dedicated-IPv4 app,
# $FLY_APP_NAME.fly.dev resolves to that dedicated v4 — the address the browser
# reaches. Verify this on the live VM during the operator pass (see runbook).
if [ -n "${LIVEKIT_NODE_IP:-}" ]; then
  NODE_IP="${LIVEKIT_NODE_IP}"
elif [ -n "${FLY_APP_NAME:-}" ]; then
  NODE_IP="$(getent hosts "${FLY_APP_NAME}.fly.dev" | awk '{print $1}' | head -n1)"
else
  NODE_IP=""
fi
if [ -z "${NODE_IP}" ]; then
  echo "FATAL: could not determine NODE_IP (set LIVEKIT_NODE_IP, or ensure FLY_APP_NAME resolves)" >&2
  exit 1
fi
echo "livekit entrypoint: advertising node_ip=${NODE_IP}" >&2

# --- keys ---------------------------------------------------------------------
if [ -z "${LIVEKIT_KEYS:-}" ]; then
  echo "FATAL: LIVEKIT_KEYS secret is not set (run: fly secrets set LIVEKIT_KEYS='apikey: secret')" >&2
  exit 1
fi

# --- launch -------------------------------------------------------------------
# --keys is read from $LIVEKIT_KEYS automatically; we pass --config, --node-ip,
# and bind to Fly's required address (satisfies UDP; TCP works on it too).
exec livekit-server \
  --config "${CONFIG_PATH}" \
  --node-ip "${NODE_IP}" \
  --bind "fly-global-services"
