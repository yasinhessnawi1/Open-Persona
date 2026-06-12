# persona-sandbox image — spec 25 T07 (full sci-Python stack)

The hardened sandbox image consumed by [`LocalDockerSandbox`](../local_docker.py)
(spec-12 T05a/b/c). Specs **16** (document generation) and **17** (data
analysis) depend on this image's preinstalled stack. Spec 25 T07 (D-25-1 /
R-25-2) expands the package set from the original eight to **31 top-level
packages**. The image tag stays `persona-sandbox:0.1.0` (the `DEFAULT_IMAGE`
constant in [`local_docker.py`](../local_docker.py) pins it); the content
change is captured by the OCI `created` label and this manifest.

## What's in here

| File | Purpose |
|---|---|
| [`Dockerfile`](Dockerfile) | Multi-stage build; ~415–485 MB compressed estimate (≤ 500 MB cap) |
| [`requirements.in`](requirements.in) | Top-level pinned dep list (versions only) — the 31-package D-25-1 set |
| [`requirements.txt`](requirements.txt) | Hash-verified resolved lock — regenerated from `requirements.in` (see "Regenerating the lock") |

## Why a separate image (not the runtime venv)

- **Threat model split (D-12-13):** the sandbox image runs untrusted
  model-generated code. The Python runtime that runs the *application* has
  no place inside that same surface — the host's `persona` venv stays
  separate.
- **Reproducibility:** the hash-mode pin (`pip install --require-hashes`)
  guarantees that a compromised PyPI mirror, BGP hijack, or yank-and-republish
  attack cannot substitute a malicious wheel — `pip` computes sha256 of the
  downloaded artefact and refuses any mismatch (PyPA's strongest pip-level
  guarantee, stronger than version pinning alone). It also neutralises the
  active `sympy-dev` typosquat-miner campaign (R-25-2 OQ-R2-5) — *provided*
  regen always resolves the canonical PyPI names in `requirements.in` against
  canonical PyPI.
- **Image-pull latency is bounded:** the layer cache means a `docker build`
  rebuild only re-runs `pip install` when `requirements.txt` changes.

## D-25-1 manifest (R-25-2 pinned versions)

The 31 top-level packages and their approximate installed sizes (R-25-2
estimates, mid-2026). Sizes are *installed*, not compressed; shared
transitives (`numpy`, `lxml`, `Pillow`) count once on disk.

| Package | Pinned version | Approx installed size | Import name / notes |
|---|---|---|---|
| `numpy` | `2.1.3` | ~40 MB | OpenBLAS (slim default; smaller than MKL) |
| `scipy` | `1.14.1` | ~95 MB | Pulls `numpy`; large native BLAS/LAPACK surface |
| `pandas` | `2.2.3` | ~55 MB | Pulls `numpy`, `python-dateutil`, `pytz`, `tzdata` |
| `sympy` | `1.13.3` | ~40 MB | Pulls `mpmath`; pure-Python |
| `statsmodels` | `0.14.4` | ~30 MB | Pulls `scipy`, `pandas`, `patsy` |
| `scikit-learn` | `1.5.2` | ~35 MB | Pulls `scipy`, `joblib`, `threadpoolctl`; imports as `sklearn` |
| `networkx` | `3.4.2` | ~10 MB | Pure-Python graphs |
| `matplotlib` | `3.9.2` | ~75 MB | Pulls `Pillow`, `kiwisolver`, `pyparsing`, `fonttools`, `contourpy`, `cycler` |
| `seaborn` | `0.13.2` | ~3 MB | Pulls `matplotlib`, `pandas` (shared) |
| `graphviz` | `0.20.3` | <1 MB | PyPI wrapper only — needs apt `graphviz` for the `dot` binary |
| `openpyxl` | `3.1.5` | ~7 MB | Pulls `et-xmlfile` (pure Python) |
| `python-docx` | `1.1.2` | ~6 MB | Pulls `lxml` (~12 MB native); imports as `docx` |
| `python-pptx` | `1.0.2` | ~9 MB | Pulls `lxml` (shared), `Pillow` (shared), `XlsxWriter`; imports as `pptx` |
| `tabulate` | `0.9.0` | <1 MB | Pure-Python table formatting |
| `reportlab` | `4.2.5` | ~20 MB | Pulls `Pillow` (shared), `chardet` |
| `pypdf` | `5.1.0` | ~3 MB | Pure-Python PDF read/write |
| `weasyprint` | `63.1` | ~6 MB + apt | HTML→PDF; pulls `cffi`, `tinycss2`, `pydyf`, `fonttools` (shared); needs apt pango/cairo libs |
| `markdown` | `3.7` | ~1 MB | Pure-Python |
| `pygments` | `2.18.0` | ~6 MB | Zero-delta promotion of an existing transitive |
| `jinja2` | `3.1.4` | ~2 MB | Pulls `MarkupSafe` (native) |
| `beautifulsoup4` | `4.12.3` | ~1 MB | Pulls `soupsieve`; imports as `bs4` |
| `lxml` | `5.3.0` | ~12 MB | Native; zero-delta promotion of an existing transitive |
| `pillow` | `11.0.0` | ~30 MB | Native; zero-delta promotion of an existing transitive; imports as `PIL` |
| `pyyaml` | `6.0.2` | ~1 MB | Native libyaml binding; imports as `yaml` |
| `jsonschema` | `4.23.0` | ~2 MB | Pulls `attrs`, `referencing`, `rpds-py` (native) |
| `httpx` | `0.28.1` | ~2 MB | Pulls `httpcore`, `h11`, `anyio`, `certifi`, `idna`, `sniffio` |
| `requests` | `2.32.3` | ~2 MB | Pulls `urllib3`, `certifi`, `charset-normalizer`, `idna` |
| `rich` | `13.9.4` | ~5 MB | Pulls `markdown-it-py`, `pygments` (shared) |
| `pytest` | `8.3.4` | ~5 MB | Pulls `pluggy`, `iniconfig`, `packaging` |
| `hypothesis` | `6.122.3` | ~6 MB | Pulls `attrs` (shared), `sortedcontainers` |
| `ipykernel` | `6.29.5` | ~12 MB | Pulls `ipython`, `jupyter-client`, `tornado`, `pyzmq` (native), `traitlets`, `debugpy`. KEPT per D-25-X-ipykernel-retained (largest remaining trim candidate) |

**Dropped from the curated set** (D-25-1; Phase-1 sign-off (b) authorization):

- **plotly** — ~50–60 MB for HTML-only output in a headless file-artifact
  sandbox; static export needs kaleido + Chromium (+150–280 MB). `matplotlib`
  + `seaborn` cover charting.
- **opencv-python-headless** — 149 MB installed; busts the 500 MB cap. `Pillow`
  covers basic image ops.
- **toml** — unmaintained since 2020; stdlib `tomllib` (3.11+) covers reads.

**Estimated final compressed image:** ~415–485 MB (D-25-1 estimate; R-12-3
hard cap ≤ 500 MB). See the build-gate below.

### Build-gate (D-25-1, locked)

The size estimate has only ~15 MB of headroom at the upper bound, so the
build is gated on a real measurement:

1. Build the image (below).
2. Measure compressed size: `docker image inspect persona-sandbox:0.1.0 --format '{{.Size}}'` for the uncompressed on-disk size, or `docker save persona-sandbox:0.1.0 | gzip -c | wc -c` for the compressed-transfer size.
3. **If compressed size > 500 MB, cut in this order:**
   1. `weasyprint` + its six apt libs (`libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 libffi8 shared-mime-info`) — ~−40 MB. (Weasyprint also carries the heaviest CVE surface; see the network invariant below.)
   2. then `hypothesis` + `pytest` — ~−6 MB.

The build, the measurement, and any cut are an **operator/CI step** — they
need network access and the image's own interpreter and are out of scope for
the code-authoring task that produced these files.

## Regenerating the lock

`requirements.txt` is **pip-compile-generated — never hand-edit it.** Regen
whenever `requirements.in` changes:

```bash
# Run INSIDE the image's interpreter (python:3.11-slim-bookworm) so the
# resolved environment markers match the runtime. Resolving on a 3.12 host
# (the prior lock header records Python 3.12) risks marker drift; all 31
# packages support both 3.11 and 3.12, but the lock must be produced on the
# interpreter that will install it (R-25-2 OQ-R2-3).
docker run --rm -v "$PWD":/io -w /io python:3.11-slim-bookworm sh -c '
    pip install pip-tools &&
    piptools compile --generate-hashes --output-file=requirements.txt requirements.in
'
```

`--require-hashes` (used by the Dockerfile) forbids unpinned deps, so the
regenerated lock is fully resolved and every wheel is sha256-verified. The
lock grows from ~50 hashed entries (the old eight-package set) to ~85–90.

> **NOTE:** the committed `requirements.txt` still reflects the *previous*
> eight-package set until this regen is run against the new `requirements.in`;
> regenerating it is the first operator/CI step of the T07 image build.

## Building

```bash
# 1. (One-time, on change) Regenerate the hashed lock — see "Regenerating
#    the lock" above. Commit the result so subsequent builds are byte-reproducible.

# 2. Build the image
docker build -t persona-sandbox:0.1.0 .

# 3. Smoke test (verifies every preinstalled lib imports cleanly, that the
#    graphviz `dot` binary is on PATH, and that weasyprint's pango/cairo
#    cffi stack loads)
docker run --rm persona-sandbox:0.1.0
# Expected output:
#   persona-sandbox ready
```

The Dockerfile's `CMD` runs the smoke test directly when no override is
given, so the build is self-validating: a broken pin, missing transitive, or
missing apt lib fails the `docker run` cleanly before the image is tagged for
production.

## Verifying R-12-2 hardening with the image

After building, the [`packages/core/tests/integration/sandbox/test_security_suite.py`](../../../../tests/integration/sandbox/test_security_suite.py)
suite parametrises every adversarial attack from [`_attacks.py`](../../../../tests/integration/sandbox/_attacks.py)
against `LocalDockerSandbox` running this image. Run:

```bash
uv run pytest packages/core/tests/integration/sandbox/ -m integration
```

Failures here are real security regressions; passes prove the §9
acceptance contract (#5 filesystem, #6 network-off, #7 metadata endpoint,
#8 resource limits, #9 no priv-esc) holds against the chosen substrate.

## Known limitations

- **`weasyprint` is safe ONLY while network-disabled is the default**
  (R-25-2 OQ-R2-2) — its CVE class is SSRF / local-file-disclosure, neutralised
  by the Spec-12 default of `network=none` + uid 65534. The invariant:
  weasyprint stays in the image only while network-off remains the sandbox
  default. If a future spec defaults the sandbox to network-on, weasyprint
  must be re-evaluated or cut.
- **IPython kernel is preinstalled but not yet exercised** — `LocalDockerSandbox`
  T05c dispatches via `docker exec` (filesystem-level session state). The
  v0.2 work that lands true variable-persistent sessions consumes
  `ipykernel` directly; the image is forward-compatible. Kept per
  D-25-X-ipykernel-retained; it is the largest remaining trim candidate if a
  future spec confirms the exec-only path is permanent.
- **Image size is an estimate until the build runs** — the ~415–485 MB figure
  is the D-25-1 estimate; the operator/CI build measures the real compressed
  size and applies the build-gate cuts above if it exceeds 500 MB.
