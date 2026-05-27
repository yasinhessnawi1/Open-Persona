#!/usr/bin/env bash
# Mirror the Persona-RAG project into packages/core/research/.
#
# Persona-RAG (the predecessor project at ~/Desktop/Desktop/Persona-RAG) holds
# the persona-vector and drift-detection research that informs persona-core but
# is not part of its public API. This script copies it in as a read-only
# reference snapshot. Re-run whenever you want to refresh the mirror.
#
# Usage:
#   packages/core/scripts/sync-research.sh                 # default source + dest
#   PERSONA_RAG_DIR=/path/to/Persona-RAG ./sync-research.sh
#
# After syncing, the source commit SHA (if Persona-RAG is a git repo) is
# written to packages/core/research/RESEARCH_VERSION so the mirror's
# provenance is recorded in Open-Persona's git history.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CORE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEST_DIR="${CORE_DIR}/research"
SRC_DIR="${PERSONA_RAG_DIR:-${HOME}/Desktop/Desktop/Persona-RAG}"

if [[ ! -d "${SRC_DIR}" ]]; then
  echo "error: Persona-RAG source not found at ${SRC_DIR}" >&2
  echo "       set PERSONA_RAG_DIR to override." >&2
  exit 1
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "error: rsync not found on PATH" >&2
  exit 1
fi

mkdir -p "${DEST_DIR}"

# Preserve the README that explains what this directory is for.
README_BACKUP=""
if [[ -f "${DEST_DIR}/README.md" ]]; then
  README_BACKUP="$(mktemp)"
  cp "${DEST_DIR}/README.md" "${README_BACKUP}"
fi

echo "syncing ${SRC_DIR}/ -> ${DEST_DIR}/"
# Apple's bundled rsync is 2.6.9 (no --info=progress2). --itemize-changes + -v
# prints one line per file as it's processed — no idle pauses, unlike
# --progress which is per-file and looks like a hang on large files.
#
# --filter='- pattern' is more reliable than --exclude on rsync 2.6.9 for
# paths that must be skipped during the *scan* (e.g. .venv with ~47k files
# and weird permissions on this machine). Filters apply earlier in the pipeline.
rsync -a --delete -v --itemize-changes \
  --filter='- .git/' \
  --filter='- .venv/' \
  --filter='- __pycache__/' \
  --filter='- .pytest_cache/' \
  --filter='- .mypy_cache/' \
  --filter='- .ruff_cache/' \
  --filter='- .ipynb_checkpoints/' \
  --filter='- .idea/' \
  --filter='- .vscode/' \
  --filter='- .claude/' \
  --filter='- .DS_Store' \
  --filter='- *.pyc' \
  --filter='- *.pyo' \
  --filter='- *.so' \
  --filter='- *$py.class' \
  --filter='- *.swp' \
  --filter='- *.swo' \
  --filter='- *~' \
  --filter='- uv.lock' \
  --filter='- node_modules/' \
  --filter='- results/' \
  --filter='- outputs/' \
  --filter='- multirun/' \
  --filter='- wandb/' \
  --filter='- data/' \
  --filter='- benchmarks_data/' \
  --filter='- .cache/' \
  --filter='- hf_cache/' \
  --filter='- .chroma/' \
  --filter='- *.bin' \
  --filter='- *.safetensors' \
  --filter='- *.pt' \
  --filter='- *.pth' \
  --filter='- *.onnx' \
  --filter='- *.gguf' \
  --filter='- .env' \
  --filter='- .env.*' \
  --filter='+ .env.example' \
  --filter='- personas/private/' \
  --filter='- RESEARCH_VERSION' \
  --filter='- docs/' \
  --filter='- CLAUDE.md' \
  --filter='- .gitignore' \
  "${SRC_DIR}/" \
  "${DEST_DIR}/"

# Restore the Open-Persona-side README (it documents the *mirror*, not the
# source). The original Persona-RAG README is still available via git history
# in the source repo if needed.
if [[ -n "${README_BACKUP}" ]]; then
  mv "${README_BACKUP}" "${DEST_DIR}/README.md"
fi

# Record the source commit SHA so we know which Persona-RAG snapshot is
# vendored. Falls back to a timestamp if the source isn't a git repo.
VERSION_FILE="${DEST_DIR}/RESEARCH_VERSION"
if git -C "${SRC_DIR}" rev-parse HEAD >/dev/null 2>&1; then
  SHA="$(git -C "${SRC_DIR}" rev-parse HEAD)"
  BRANCH="$(git -C "${SRC_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  DIRTY=""
  if ! git -C "${SRC_DIR}" diff --quiet || ! git -C "${SRC_DIR}" diff --cached --quiet; then
    DIRTY=" (dirty working tree)"
  fi
  {
    echo "source:  ${SRC_DIR}"
    echo "commit:  ${SHA}${DIRTY}"
    echo "branch:  ${BRANCH}"
    echo "synced:  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "${VERSION_FILE}"
else
  {
    echo "source:  ${SRC_DIR}"
    echo "commit:  (not a git repo)"
    echo "synced:  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "${VERSION_FILE}"
fi

echo "done. snapshot info -> ${VERSION_FILE}"
