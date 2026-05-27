"""Vendor PersonaGym (https://github.com/vsamuel2003/PersonaGym, MIT) into ``benchmarks_data/personagym/``.

Fetches the upstream ``code/personas.py`` and ``questions/benchmark-v1/*.json``
files via the GitHub raw HTTP endpoint, then writes them in the layout
:func:`persona_rag.benchmarks.personagym.load_personagym` expects::

    benchmarks_data/personagym/
    ├── personas.json
    └── questions/<persona text>.json

Idempotent: re-running overwrites with fresh upstream content. Honours
``--ref`` for pinning to a specific commit / tag (default: ``master``).

Usage::

    uv run python scripts/fetch_personagym.py
    uv run python scripts/fetch_personagym.py --ref <commit_sha>

The script depends only on ``urllib`` so it works on any installed
Python; no project import required.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GITHUB_API = "https://api.github.com/repos/vsamuel2003/PersonaGym/contents"
GITHUB_RAW = "https://raw.githubusercontent.com/vsamuel2003/PersonaGym"


def _fetch(url: str, *, retries: int = 3, sleep_s: float = 1.5) -> bytes:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "persona-rag-fetch-personagym"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception as exc:
            last_err = exc
            if attempt == retries - 1:
                break
            time.sleep(sleep_s * (attempt + 1))
    assert last_err is not None
    raise last_err


def fetch_personas_list(ref: str) -> list[str]:
    raw = _fetch(f"{GITHUB_RAW}/{ref}/code/personas.py").decode("utf-8")
    namespace: dict[str, object] = {}
    exec(compile(raw, "personas.py", "exec"), namespace)
    personas = namespace.get("benchmark_personas")
    if not isinstance(personas, list):
        raise RuntimeError("PersonaGym personas.py did not define `benchmark_personas` as a list")
    return [str(p) for p in personas]


def fetch_question_files(ref: str, target_dir: Path, personas: list[str]) -> int:
    """Fetch one ``<persona>.json`` per persona via the raw endpoint.

    We avoid the GitHub API ``contents`` endpoint here because it is rate-
    limited unauthenticated (60 req/h) and pagination on a 200-file dir
    eats two requests; the raw CDN has no listing but is rate-limited far
    more generously and accepts URL-encoded filenames directly.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    n_written = 0
    n_skipped = 0
    for persona_text in personas:
        name = f"{persona_text}.json"
        out_path = target_dir / name
        if out_path.exists() and out_path.stat().st_size > 0:
            n_skipped += 1
            continue
        download_url = f"{GITHUB_RAW}/{ref}/questions/benchmark-v1/{urllib.parse.quote(name)}"
        try:
            body = _fetch(download_url)
        except Exception as exc:
            print(f"  WARN: skip {name!r}: {exc}", file=sys.stderr)
            continue
        out_path.write_bytes(body)
        n_written += 1
        if n_written % 25 == 0:
            print(f"  fetched {n_written} question files", file=sys.stderr)
    if n_skipped:
        print(f"  skipped {n_skipped} already-vendored question files", file=sys.stderr)
    return n_written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--ref", default="master", help="git ref (branch / tag / sha) to vendor from"
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "benchmarks_data" / "personagym",
    )
    args = parser.parse_args()
    output_root: Path = args.output_root
    output_root.mkdir(parents=True, exist_ok=True)

    print(f"PersonaGym: vendoring ref={args.ref} into {output_root}", file=sys.stderr)
    personas = fetch_personas_list(args.ref)
    (output_root / "personas.json").write_text(
        json.dumps(personas, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"PersonaGym: wrote {len(personas)} personas to personas.json", file=sys.stderr)

    n_qfiles = fetch_question_files(args.ref, output_root / "questions", personas)
    print(
        f"PersonaGym: wrote {n_qfiles} question files to questions/ (ref={args.ref})",
        file=sys.stderr,
    )

    (output_root / "VENDORED.txt").write_text(
        f"source: https://github.com/vsamuel2003/PersonaGym\nref: {args.ref}\n"
        f"license: MIT\nfetched_via: scripts/fetch_personagym.py\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
