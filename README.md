# Open Persona

An open-source platform for building and running AI personas with typed memory, multi-model routing, and agentic task execution.

## Repository structure

```
packages/
  core/       persona-core — the open-source library (Apache 2.0)
  runtime/    persona-runtime — the generation loop, router, and agentic engine
  api/        persona-api — the hosted FastAPI service
  web/        persona-web — the Next.js web application
specs/        specification documents for each component
docs/         research notes, decisions, and project documentation
```

## Development setup

```bash
uv sync
uv run pytest
uv run mypy packages/core/src
uv run ruff check
```

### Cross-package PYTHONPATH (editor / ad-hoc `python -c`)

`uv run` resolves the four workspace `src/` roots automatically. For editor
integrations, `mypy --strict`, or one-off `python -c "import persona_api"`
shells, source `scripts/devenv.sh` once per shell to pin the same paths
(mirrors `[tool.mypy] mypy_path` in `pyproject.toml`):

```bash
source scripts/devenv.sh
```

### macOS + iCloud `.venv` workaround (`.pth` hidden-flag)

If your `.venv` lives inside an iCloud-managed folder (Desktop / Documents)
and a fresh `uv sync` produces `ModuleNotFoundError: No module named
'persona_api'` despite the workspace `.pth` files existing, macOS has set
`UF_HIDDEN` on them — `site.py` silently skips hidden `.pth` files. Fix:

```bash
chflags nohidden .venv/lib/python3.12/site-packages/*.pth
```

See `docs/MAINTENANCE.md` ("iCloud `.pth` hidden-flag surprise" row) for the
underlying CPython behaviour and diagnosis command.

## License

`packages/core/` is licensed under Apache 2.0. All other packages are private.
