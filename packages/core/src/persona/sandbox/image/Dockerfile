# persona-sandbox:0.1.0 — D-25-1 / R-25-2 full sci-Python sandbox image
# (spec 25 T07; supersedes the spec-12 T06 R-12-3 eight-package image).
#
# Multi-stage build:
#   Stage 1 (builder) — installs the pinned data/document/science stack into
#                       an isolated venv using ``--require-hashes`` (PyPA's
#                       strongest pip-level supply-chain guarantee).
#   Stage 2 (runtime) — copies the venv into a minimal image with the
#                       runtime libs only (no compilers, no apt cache).
#
# Estimated compressed image size: ~415–485 MB (D-25-1 estimate; R-12-3 hard
# cap ≤ 500 MB). BUILD-GATE (D-25-1, locked): after building, measure the
# compressed size; if it exceeds 500 MB, cut in order: (1) weasyprint + its
# six pango/cairo apt libs (~−40 MB), then (2) hypothesis + pytest (~−6 MB).
#
# Build:
#   docker build -t persona-sandbox:0.1.0 packages/core/src/persona/sandbox/image/
#
# The base ``python:3.11-slim-bookworm`` is the R-12-3 choice; NumPy on slim
# ships OpenBLAS (smaller + telemetry-free vs Intel MKL).
#
# syntax=docker/dockerfile:1.7

# ---------------------------------------------------------------------------
# Stage 1 — builder
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS builder

# Build-time deps for any wheels that need compilation (most have manylinux
# wheels; this is a safety net for arm64 where some still source-build).
# ``libffi-dev`` covers any source-build of weasyprint's cffi bindings
# (the runtime stage ships ``libffi8``).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev libxslt1-dev \
        zlib1g-dev libjpeg-dev libfreetype6-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv "$VIRTUAL_ENV"

COPY requirements.txt /tmp/requirements.txt

# Hash-checking mode: any wheel whose sha256 does not match is refused.
# ``--require-hashes`` also forbids unpinned deps, which forces a fully
# resolved lock (PyPA hash-mode pinning docs).
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt

# Pre-warm .pyc so first import in the read-only sandbox is fast
# (the runtime mounts /tmp as tmpfs with noexec; CPython's .pyc cache
# doesn't need exec, so this stays safe).
RUN python -m compileall -q "$VIRTUAL_ENV"


# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

# Runtime libs only — no compilers, no apt cache.
#   - libxml2/libxslt1.1, libjpeg/libfreetype  → lxml, Pillow, matplotlib
#   - graphviz                                  → the ``dot`` binary the
#                                                 ``graphviz`` Python wrapper
#                                                 shells out to (D-25-1)
#   - libpango*/libcairo2/libgdk-pixbuf/libffi8/shared-mime-info
#                                               → weasyprint's cffi-loaded
#                                                 rendering stack (D-25-1).
#                                                 NOTE (R-25-2 OQ-R2-2):
#                                                 weasyprint's CVE class is
#                                                 SSRF/file-disclosure — safe
#                                                 ONLY because the sandbox
#                                                 defaults to network=none +
#                                                 uid 65534. The invariant:
#                                                 weasyprint stays only while
#                                                 network-disabled stays the
#                                                 default.
#   - fonts-dejavu-core                         → matplotlib + weasyprint glyphs
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 libxslt1.1 \
        libjpeg62-turbo libfreetype6 \
        graphviz \
        libpango-1.0-0 libpangocairo-1.0-0 libcairo2 \
        libgdk-pixbuf-2.0-0 libffi8 shared-mime-info \
        fonts-dejavu-core \
        tini \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# UID 65534 (nobody) already exists in debian:bookworm; verify and create the
# workspace skeleton. Ownership matches the R-12-2 ``user=65534:65534``.
RUN id -u nobody >/dev/null 2>&1 || useradd -u 65534 -g 65534 -s /usr/sbin/nologin nobody \
    && mkdir -p /workspace/in /workspace/out /home/nobody \
    && chown -R 65534:65534 /workspace /home/nobody

COPY --from=builder --chown=65534:65534 /opt/venv /opt/venv

ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=0 \
    HOME=/home/nobody \
    MPLCONFIGDIR=/home/nobody/.mpl

USER 65534:65534
WORKDIR /workspace/out

# OCI labels for provenance — Open Persona Spec 25 (full sci-Python stack).
LABEL org.opencontainers.image.title="persona-sandbox" \
      org.opencontainers.image.version="0.1.0" \
      org.opencontainers.image.created="2026-06-11T00:00:00Z" \
      org.opencontainers.image.source="https://github.com/<org>/open-persona" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.description="Hardened sandbox image for spec-12 code execution; full sci-Python stack (D-25-1; Specs 16, 17 dependents)."

# HEALTHCHECK NONE — the sandbox is ephemeral; a healthcheck would spawn
# a probe process inside every container, skewing the R-12-2 resource caps.
# Build-time validation runs the smoke test below (see README.md).
HEALTHCHECK NONE

# NO ENTRYPOINT — the SDK invocation in LocalDockerSandbox supplies the
# full command (``["python", "-u", "..."]`` for one-shot, ``["tail", "-f",
# "/dev/null"]`` for the T05c session keepalive). ``init=True`` in the SDK
# call injects Docker's own tini at PID 1, which reaps zombies for the
# T05c IPython-kernel subprocesses without us shipping a second tini in
# the image. The default CMD below runs the smoke test for direct
# ``docker run persona-sandbox:0.1.0`` invocations (no args from the SDK
# override).
# Smoke-import CMD covers every one of the D-25-1 top-level packages by its
# correct *import* name (bs4, docx, pptx, PIL, sklearn, yaml differ from their
# distribution names), asserts the graphviz ``dot`` binary is on PATH, and
# imports weasyprint to exercise the pango/cairo cffi load. A broken pin,
# missing transitive, or missing apt lib fails ``docker run`` cleanly before
# the image is tagged for production.
CMD ["python", "-c", "import shutil; assert shutil.which('dot'), 'graphviz dot binary missing'; import numpy, scipy, pandas, sympy, statsmodels, sklearn, networkx, matplotlib, seaborn, graphviz, openpyxl, docx, pptx, tabulate, reportlab, pypdf, weasyprint, markdown, pygments, jinja2, bs4, lxml, PIL, yaml, jsonschema, httpx, requests, rich, pytest, hypothesis, ipykernel; print('persona-sandbox ready')"]
