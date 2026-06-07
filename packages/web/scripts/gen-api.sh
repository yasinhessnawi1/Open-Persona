#!/usr/bin/env bash
# Regenerate the committed OpenAPI artifacts for the persona-api REST surface.
#
#   packages/web/openapi.json      <- dumped from the FastAPI app factory
#   packages/web/src/lib/api/schema.ts  <- openapi-typescript types
#
# Run after the API's request/response models change (spec 09 §5; D-09-1).
# The committed client is NOT generated at runtime — commit the output.
# NOTE: only the REST surface is covered. The SSE event shapes are hand-mirrored
# in src/lib/sse-types.ts (OpenAPI can't model SSE) — see D-09-1.
set -euo pipefail

WEB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
API_DIR="$WEB_DIR/../api"
REPO_ROOT="$WEB_DIR/../.."
OUT_JSON="$WEB_DIR/openapi.json"
OUT_TS="$WEB_DIR/src/lib/api/schema.ts"

# uv 0.6.x writes editable installs as `_editable_impl_*.pth`; CPython 3.13
# treats leading-underscore .pth files as hidden during site-init, so
# ``import persona_api`` fails even though the package is "installed."
# Repo conftest.py applies the same PYTHONPATH workaround for pytest; this
# script applies it for the one-off OpenAPI dump (mirrors the Spec 01 §D-01-9
# surprise). Drop when uv ships a release without the underscore prefix.
PYTHONPATH_PREFIX="$REPO_ROOT/packages/core/src:$REPO_ROOT/packages/runtime/src:$REPO_ROOT/packages/api/src"

echo "→ Dumping /openapi.json from the persona-api app factory…"
( cd "$API_DIR" && PYTHONPATH="$PYTHONPATH_PREFIX${PYTHONPATH:+:$PYTHONPATH}" uv run python -c \
  "import json; from persona_api.app import create_app; print(json.dumps(create_app().openapi(), indent=2))" \
) > "$OUT_JSON"

echo "→ Generating TypeScript types → ${OUT_TS#"$WEB_DIR"/}"
pnpm exec openapi-typescript "$OUT_JSON" -o "$OUT_TS"

echo "→ Formatting generated output to satisfy Biome…"
( cd "$WEB_DIR" && pnpm exec biome format --write "$OUT_TS" "$OUT_JSON" >/dev/null )

echo "✓ Done. Commit openapi.json + src/lib/api/schema.ts."
