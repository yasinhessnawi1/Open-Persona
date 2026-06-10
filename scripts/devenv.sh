# Open Persona dev-env shim — source this from a dev shell.
#
# Pins PYTHONPATH to the four workspace `src/` roots so editor / mypy /
# ad-hoc `python -c "import persona_api"` invocations resolve cross-package
# imports the same way `uv run` does. Mirrors the [tool.mypy] mypy_path pin
# in pyproject.toml (LF-12-3 / D-19-X-mypy-path-pin chain entry 22).
#
# Usage:
#   source scripts/devenv.sh
#
# Safe to source from any cwd; resolves repo root from this file's location.
# shellcheck shell=bash

_persona_devenv_root="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"

export PYTHONPATH="\
${_persona_devenv_root}/packages/core/src:\
${_persona_devenv_root}/packages/runtime/src:\
${_persona_devenv_root}/packages/api/src:\
${_persona_devenv_root}/packages/voice/src${PYTHONPATH:+:${PYTHONPATH}}"

unset _persona_devenv_root
