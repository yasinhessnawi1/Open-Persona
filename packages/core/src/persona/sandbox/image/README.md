# persona-sandbox image — spec 12 T06

The hardened sandbox image consumed by [`LocalDockerSandbox`](../local_docker.py)
(spec-12 T05a/b/c). Specs **16** (document generation) and **17** (data
analysis) depend on this image's preinstalled stack.

## What's in here

| File | Purpose |
|---|---|
| [`Dockerfile`](Dockerfile) | Multi-stage build (R-12-3); ~290–340 MB compressed target |
| [`requirements.in`](requirements.in) | Top-level pinned dep list (versions only) |
| [`requirements.txt`](requirements.txt) | Hash-verified resolved lock — **placeholders until `pip-compile` runs** |

## Why a separate image (not the runtime venv)

- **Threat model split (D-12-13):** the sandbox image runs untrusted
  model-generated code. The Python runtime that runs the *application* has
  no place inside that same surface — the host's `persona` venv stays
  separate.
- **Reproducibility:** the hash-mode pin (`pip install --require-hashes`)
  guarantees that a compromised PyPI mirror, BGP hijack, or yank-and-republish
  attack cannot substitute a malicious wheel — `pip` computes sha256 of the
  downloaded artefact and refuses any mismatch (PyPA's strongest pip-level
  guarantee, stronger than version pinning alone).
- **Image-pull latency is bounded:** the layer cache means a `docker build`
  rebuild only re-runs `pip install` when `requirements.txt` changes.

## D-12-2 manifest (R-12-3 pinned versions)

Top-level packages and their approximate installed sizes
(R-12-3 measurements, mid-2026):

| Package | Pinned version | Approx installed size | Notes |
|---|---|---|---|
| `numpy` | `2.1.3` | ~40 MB | OpenBLAS (slim default; smaller than MKL) |
| `pandas` | `2.2.3` | ~55 MB | Pulls `numpy`, `python-dateutil`, `pytz`, `tzdata` |
| `matplotlib` | `3.9.2` | ~75 MB | Pulls `Pillow` (~30 MB), `kiwisolver`, `pyparsing`, `fonttools`, `contourpy`, `cycler` |
| `openpyxl` | `3.1.5` | ~7 MB | Pulls `et-xmlfile` (pure Python) |
| `python-docx` | `1.1.2` | ~6 MB | Pulls `lxml` (~12 MB native); imports as `docx` |
| `python-pptx` | `1.0.2` | ~9 MB | Pulls `lxml` (shared), `Pillow` (shared), `XlsxWriter`; imports as `pptx` |
| `reportlab` | `4.2.5` | ~20 MB | Pulls `Pillow` (shared), `chardet` |
| `ipykernel` | `6.29.5` | ~12 MB | Pulls `ipython`, `jupyter-client`, `tornado`, `pyzmq` (~6 MB native), `traitlets`, `debugpy` (~7 MB) |

Shared transitives (`Pillow`, `lxml`, `numpy`) count once on disk.

**Estimated final compressed image:** ~290–340 MB (R-12-3 target ≤ 500 MB).

## Building

```bash
# 1. (One-time) Generate the hashed lock from requirements.in
#    Run on the build host or in CI; commit the result so subsequent
#    builds are byte-reproducible.
pip install pip-tools
pip-compile --generate-hashes \
    --output-file=requirements.txt requirements.in

# 2. Build the image
docker build -t persona-sandbox:0.1.0 .

# 3. Smoke test (verifies every preinstalled lib imports cleanly)
docker run --rm persona-sandbox:0.1.0
# Expected output:
#   persona-sandbox ready
```

The Dockerfile's `CMD` runs the smoke test directly when no override is
given, so the build is self-validating: a broken pin or missing transitive
fails the `docker run` cleanly before the image is tagged for production.

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

## Known limitations (carried into the spec-12 close-out)

- **The `requirements.txt` ships with placeholder hashes** — a build host
  with PyPI access must run `pip-compile --generate-hashes` to populate
  them. CI runs this step automatically; local devs run it once after
  cloning.
- **IPython kernel is preinstalled but not yet exercised** — `LocalDockerSandbox`
  T05c dispatches via `docker exec` (filesystem-level session state). The
  v0.2 work that lands true variable-persistent sessions consumes
  `ipykernel` directly; the image is forward-compatible.
- **Image size is approximate** — the R-12-3 target of ≤500 MB compressed
  has headroom for the actual `pip-compile` result to add a few MB of
  transitive deps without rebuilding the budget.
