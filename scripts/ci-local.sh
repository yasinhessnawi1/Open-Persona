#!/usr/bin/env bash
# =============================================================================
# ci-local.sh — run what CI runs, locally, before you push / after a merge.
# =============================================================================
#
# WHY
#   Mirrors .github/workflows/ci.yml EXACTLY (same tools, flags, order) so that
#   "green locally" reliably implies "green in CI". It does NOT invent checks:
#   every step below maps to a line in ci.yml (the mapping is in each step's
#   comment). It never modifies source, never commits, never pushes — it only
#   runs checks and reports.
#
# WHAT IT MIRRORS (ci.yml job -> step here)
#   lint-and-type-check:
#     uv run ruff check                                   -> ruff-check
#     uv run ruff format --check                          -> ruff-format
#     uv run mypy core/src runtime/src voice/src connectors/src --strict -> mypy-strict
#     uv run mypy packages/api/src                        -> mypy-api
#   test:
#     uv run pytest --collect-only -q (EARLY cheap gate)  -> pytest-collect *
#     uv run pytest                                       -> pytest-unit
#   test-integration:
#     uv run pytest -m integration  (real Postgres)       -> pytest-integration
#   web:
#     pnpm install --frozen-lockfile                      -> web-install
#     pnpm typecheck / lint / check:no-literals / build / test
#                                                         -> web-typecheck ...
#
#   (*) pytest-collect is NOT a literal CI step, but it catches the cross-package
#       test-file basename collision ("import file mismatch") that has broken CI
#       repeatedly — in seconds, before the slow full run. Treated as a hard gate.
#
# USAGE
#   After a merge (full honest run, default):
#       ./scripts/ci-local.sh
#
#   Fast pre-push (lint + types + collect-only + unit; DEFER integration + web):
#       ./scripts/ci-local.sh --fast
#
#   Skip the (slow, Postgres-dependent) integration leg explicitly:
#       ./scripts/ci-local.sh --no-integration      (or SKIP_INTEGRATION=1)
#
#   Skip the web (pnpm) leg:
#       ./scripts/ci-local.sh --no-web              (or SKIP_WEB=1)
#
#   Stop at the first failing step (CI is NOT fail-fast across jobs, so the
#   default here runs everything and reports the full picture):
#       ./scripts/ci-local.sh --fail-fast
#
#   Combine freely, e.g.:  ./scripts/ci-local.sh --no-web --no-integration
#
# GIT PRE-PUSH HOOK
#   This same script backs the opt-in pre-push hook. Install:
#       ln -sf ../../scripts/pre-push.hook .git/hooks/pre-push   # from repo root
#   Bypass in an emergency:
#       git push --no-verify
#   The hook runs in --fast mode by default (see scripts/pre-push.hook).
#
# EXIT CODE
#   0  iff every step that RAN passed. Non-zero if any ran-step failed.
#   A SKIPPED integration/web leg is reported LOUDLY in the summary and does NOT
#   silently turn the run green — the summary always states what did not run.
#
# CANNOT BE REPRODUCED LOCALLY (faithfully noted, not faked)
#   - CI's HuggingFace embedder-cache warm step (actions/cache + HF 429 retry):
#     locally the bge-small-en-v1.5 model is already in ~/.cache/huggingface, so
#     the integration suite's first persona-create finds it on disk. We probe for
#     it and warn if absent rather than re-implementing the retry/backoff loop.
#   - CI's `next build` runs with Clerk CI-placeholder env. We replicate those
#     placeholders for the web build so it type-checks + bundles the same way.
# =============================================================================
set -uo pipefail

# --- Resolve repo root (script lives in scripts/) ----------------------------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
cd "$REPO_ROOT"

# --- Flags / env -------------------------------------------------------------
FAST=0
FAIL_FAST=0
SKIP_INTEGRATION="${SKIP_INTEGRATION:-0}"
SKIP_WEB="${SKIP_WEB:-0}"

for arg in "$@"; do
  case "$arg" in
    --fast)            FAST=1 ;;
    --no-integration)  SKIP_INTEGRATION=1 ;;
    --no-web)          SKIP_WEB=1 ;;
    --fail-fast)       FAIL_FAST=1 ;;
    -h|--help)
      sed -n '2,70p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "ci-local: unknown argument: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

# Fast mode defers the slow legs (integration + web) to keep pre-push snappy.
if [[ "$FAST" -eq 1 ]]; then
  SKIP_INTEGRATION=1
  SKIP_WEB=1
fi

# --- Local Postgres / integration DB config ----------------------------------
# CI uses a disposable ephemeral Postgres. Locally the dev Postgres on :5436 is
# the SHARED dev database and the integration fixtures DROP SCHEMA public CASCADE
# — running them against the dev `persona` DB would WIPE dev data. So we target a
# separate disposable DB whose name ends in `_test` (`persona_test`), which also
# satisfies the conftest safety gate. Override via PERSONA_TEST_DB_NAME / the URLs.
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${POSTGRES_HOST_PORT:-5436}"
TEST_DB_NAME="${PERSONA_TEST_DB_NAME:-persona_test}"
# These mirror ci.yml's env block (sync psycopg3 dialect, D-07-1), pointed at the
# local disposable _test DB instead of CI's ephemeral one.
CI_DATABASE_URL="${CI_LOCAL_DATABASE_URL:-postgresql+psycopg://persona:persona@${PG_HOST}:${PG_PORT}/${TEST_DB_NAME}}"
CI_APP_DATABASE_URL="${CI_LOCAL_APP_DATABASE_URL:-postgresql+psycopg://persona_app:persona_app@${PG_HOST}:${PG_PORT}/${TEST_DB_NAME}}"

# --- Output helpers ----------------------------------------------------------
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'
  C_YEL=$'\033[33m'; C_BLUE=$'\033[34m'; C_BOLD=$'\033[1m'
else
  C_RESET=''; C_RED=''; C_GREEN=''; C_YEL=''; C_BLUE=''; C_BOLD=''
fi

# Parallel arrays of step results for the final summary.
declare -a STEP_NAMES=()
declare -a STEP_STATUS=()   # PASS | FAIL | SKIP
declare -a STEP_NOTE=()
ANY_FAIL=0

hr()    { printf '%s\n' "------------------------------------------------------------"; }
banner(){ printf '\n%s== %s ==%s\n' "$C_BOLD$C_BLUE" "$1" "$C_RESET"; }

record() { # name status note
  STEP_NAMES+=("$1"); STEP_STATUS+=("$2"); STEP_NOTE+=("${3:-}")
  case "$2" in
    FAIL) ANY_FAIL=1 ;;
  esac
}

# run_step "label" "ci.yml ref" cmd...
run_step() {
  local label="$1"; local ref="$2"; shift 2
  banner "$label  ${C_RESET}${C_YEL}(ci.yml: ${ref})${C_RESET}"
  printf '%s$ %s%s\n' "$C_BOLD" "$*" "$C_RESET"
  hr
  local start end rc
  start=$(date +%s)
  "$@"
  rc=$?
  end=$(date +%s)
  hr
  if [[ $rc -eq 0 ]]; then
    printf '%s[PASS]%s %s (%ss)\n' "$C_GREEN" "$C_RESET" "$label" "$((end-start))"
    record "$label" PASS "$((end-start))s"
  else
    printf '%s[FAIL]%s %s (exit %d, %ss)\n' "$C_RED" "$C_RESET" "$label" "$rc" "$((end-start))"
    record "$label" FAIL "exit ${rc}"
    if [[ "$FAIL_FAST" -eq 1 ]]; then
      printf '%s--fail-fast set: stopping at first failure.%s\n' "$C_RED" "$C_RESET"
      summary
      exit 1
    fi
  fi
  return 0
}

skip_step() { # label reason
  banner "$1  ${C_RESET}${C_YEL}(SKIPPED)${C_RESET}"
  printf '%s[SKIP]%s %s — %s\n' "$C_YEL" "$C_RESET" "$1" "$2"
  record "$1" SKIP "$2"
}

# --- Special handling: collect-only is a HARD gate ---------------------------
# A non-zero exit here means a collection/import error (e.g. duplicate test
# basenames across packages -> "import file mismatch"). pytest exit 5 == "no
# tests collected", which for THIS repo (>1000 tests, CI treats empty as a
# failure) is also a hard failure. Anything non-zero => fail the gate.
run_collect_gate() {
  banner "pytest-collect (early cheap gate)  ${C_RESET}${C_YEL}(catches cross-package basename collisions)${C_RESET}"
  printf '%s$ uv run pytest --collect-only -q%s\n' "$C_BOLD" "$C_RESET"
  hr
  local out rc
  out="$(uv run pytest --collect-only -q 2>&1)"
  rc=$?
  printf '%s\n' "$out" | tail -n 25
  hr
  if [[ $rc -eq 0 ]]; then
    printf '%s[PASS]%s pytest-collect\n' "$C_GREEN" "$C_RESET"
    record "pytest-collect" PASS ""
  else
    printf '%s[FAIL]%s pytest-collect (exit %d)\n' "$C_RED" "$C_RESET" "$rc"
    if printf '%s' "$out" | grep -qi 'import file mismatch'; then
      printf '%s>>> COLLECTION ERROR: duplicate test-file basename across packages.%s\n' "$C_RED" "$C_RESET"
      printf '    Two test files share a basename (e.g. test_foo.py in two packages).\n'
      printf '    Rename one so every test file basename is unique across the workspace.\n'
      printf '    Offending lines:\n'
      printf '%s' "$out" | grep -i 'import file mismatch' | sed 's/^/      /'
    fi
    record "pytest-collect" FAIL "exit ${rc}"
    if [[ "$FAIL_FAST" -eq 1 ]]; then summary; exit 1; fi
    return 1
  fi
  return 0
}

# --- Integration-DB reachability probe ---------------------------------------
# Returns 0 if the disposable _test DB is reachable as BOTH roles ci.yml needs
# (superuser DATABASE_URL + persona_app APP_DATABASE_URL for the RLS suite).
probe_integration_db() {
  uv run python - "$CI_DATABASE_URL" "$CI_APP_DATABASE_URL" <<'PY'
import sys
from sqlalchemy import create_engine, text
for url in sys.argv[1:3]:
    try:
        with create_engine(url).connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — best-effort reachability probe
        print(f"unreachable: {url.rsplit('@',1)[-1]} ({exc.__class__.__name__})", file=sys.stderr)
        sys.exit(1)
sys.exit(0)
PY
}

summary() {
  printf '\n%s============================ SUMMARY ============================%s\n' "$C_BOLD" "$C_RESET"
  local i
  for i in "${!STEP_NAMES[@]}"; do
    local st="${STEP_STATUS[$i]}" color sym
    case "$st" in
      PASS) color="$C_GREEN"; sym="PASS" ;;
      FAIL) color="$C_RED";   sym="FAIL" ;;
      SKIP) color="$C_YEL";   sym="SKIP" ;;
    esac
    printf '  %s%-4s%s  %-22s %s\n' "$color" "$sym" "$C_RESET" "${STEP_NAMES[$i]}" "${STEP_NOTE[$i]}"
  done
  hr
  # Loud, explicit callout if integration or web did not run — never let a
  # skipped leg masquerade as a fully-green tree.
  local skipped_notice=0
  for i in "${!STEP_NAMES[@]}"; do
    if [[ "${STEP_STATUS[$i]}" == "SKIP" ]]; then
      printf '%s!! NOT RUN: %s — %s%s\n' "$C_YEL$C_BOLD" "${STEP_NAMES[$i]}" "${STEP_NOTE[$i]}" "$C_RESET"
      skipped_notice=1
    fi
  done
  if [[ "$skipped_notice" -eq 1 ]]; then
    printf '%s   ^ CI WILL still run these. Green here does NOT cover the skipped legs.%s\n' "$C_YEL" "$C_RESET"
    hr
  fi
  if [[ "$ANY_FAIL" -eq 0 ]]; then
    printf '%s%sALL RAN STEPS PASSED.%s\n' "$C_GREEN" "$C_BOLD" "$C_RESET"
  else
    printf '%s%sONE OR MORE STEPS FAILED.%s\n' "$C_RED" "$C_BOLD" "$C_RESET"
  fi
}

# =============================================================================
# RUN
# =============================================================================
printf '%s%sci-local%s — mirroring .github/workflows/ci.yml\n' "$C_BOLD" "$C_BLUE" "$C_RESET"
printf 'repo: %s\n' "$REPO_ROOT"
printf 'mode: %s | integration: %s | web: %s | fail-fast: %s\n' \
  "$([[ $FAST -eq 1 ]] && echo fast || echo full)" \
  "$([[ $SKIP_INTEGRATION -eq 1 ]] && echo SKIP || echo run)" \
  "$([[ $SKIP_WEB -eq 1 ]] && echo SKIP || echo run)" \
  "$([[ $FAIL_FAST -eq 1 ]] && echo on || echo off)"

# --- ci.yml: uv sync --all-packages (both python jobs depend on this) --------
run_step "uv-sync" "uv sync --all-packages" uv sync --all-packages

# --- Job lint-and-type-check -------------------------------------------------
run_step "ruff-check"   "uv run ruff check"          uv run ruff check
run_step "ruff-format"  "uv run ruff format --check"  uv run ruff format --check
run_step "mypy-strict"  "mypy core+runtime+voice+connectors --strict"  uv run mypy packages/core/src packages/runtime/src packages/voice/src packages/connectors/src --strict
run_step "mypy-api"     "mypy packages/api/src"        uv run mypy packages/api/src

# --- Job test: early collect gate, then full default suite -------------------
run_collect_gate
run_step "pytest-unit"  "uv run pytest (default suite)" uv run pytest

# --- Job test-integration ----------------------------------------------------
if [[ "$SKIP_INTEGRATION" -eq 1 ]]; then
  skip_step "pytest-integration" "skipped by flag/env ($([[ $FAST -eq 1 ]] && echo --fast || echo --no-integration/SKIP_INTEGRATION))"
else
  banner "integration-db probe  ${C_RESET}${C_YEL}(disposable _test DB on :${PG_PORT})${C_RESET}"
  if probe_integration_db; then
    printf '%s[ok]%s reachable: %s + persona_app role\n' "$C_GREEN" "$C_RESET" "$TEST_DB_NAME"
    # Warn (do not fail) if the embedder model is not cached — first run would
    # hit the network (CI warms it with retries; we just surface the risk).
    if ! ls "${HOME}/.cache/huggingface/hub" 2>/dev/null | grep -qi 'bge-small-en-v1.5'; then
      printf '%s[warn]%s bge-small-en-v1.5 not in HF cache; first integration run will download it.\n' "$C_YEL" "$C_RESET"
    fi
    # Mirror ci.yml test-integration env block exactly.
    run_step "pytest-integration" "pytest -m integration" \
      env DATABASE_URL="$CI_DATABASE_URL" \
          APP_DATABASE_URL="$CI_APP_DATABASE_URL" \
          PERSONA_TEST_DB="1" \
          uv run pytest -m integration
  else
    skip_step "pytest-integration" "dev Postgres _test DB unreachable on :${PG_PORT} (start it: docker compose up -d postgres, ensure '${TEST_DB_NAME}' + persona_app exist)"
  fi
fi

# --- Job web (pnpm) ----------------------------------------------------------
if [[ "$SKIP_WEB" -eq 1 ]]; then
  skip_step "web" "skipped by flag/env ($([[ $FAST -eq 1 ]] && echo --fast || echo --no-web/SKIP_WEB))"
elif ! command -v pnpm >/dev/null 2>&1; then
  skip_step "web" "pnpm not installed (install: corepack enable pnpm)"
else
  WEB_DIR="$REPO_ROOT/packages/web"
  run_step "web-install"      "pnpm install --frozen-lockfile" \
    bash -c "cd '$WEB_DIR' && pnpm install --frozen-lockfile"
  run_step "web-typecheck"    "pnpm typecheck" \
    bash -c "cd '$WEB_DIR' && pnpm typecheck"
  run_step "web-lint"         "pnpm lint" \
    bash -c "cd '$WEB_DIR' && pnpm lint"
  run_step "web-no-literals"  "pnpm check:no-literals" \
    bash -c "cd '$WEB_DIR' && pnpm check:no-literals"
  # `next build` initialises Clerk at module load; CI placeholders are enough to
  # type-check + bundle (mirrors ci.yml web job env block).
  run_step "web-build"        "pnpm build (Clerk CI placeholders)" \
    bash -c "cd '$WEB_DIR' && \
      NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 \
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_ci_placeholder \
      CLERK_SECRET_KEY=sk_test_ci_placeholder \
      NEXT_PUBLIC_CLERK_JWT_TEMPLATE=persona-api \
      NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in \
      NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up \
      NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL=/personas \
      NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL=/personas \
      pnpm build"
  run_step "web-test"         "pnpm test" \
    bash -c "cd '$WEB_DIR' && pnpm test"
fi

summary
[[ "$ANY_FAIL" -eq 0 ]] && exit 0 || exit 1
