"""Root conftest: ensure workspace ``src/`` paths are importable.

Background: uv 0.6.x installs workspace members as editable via PEP 660 .pth
files prefixed ``_editable_impl_``. CPython 3.13 (and some 3.12 builds from
python.org) treat any .pth file whose name begins with ``_`` as hidden and
skip it during site-init. The net effect is that ``import persona`` fails
even though ``persona-core`` is "installed."

This conftest restores the editable behaviour by prepending each workspace
member's ``src/`` directory to ``sys.path`` before pytest collects tests.
Production code paths that go through ``python -m persona ...`` work because
the console entry-point registered in ``[project.scripts]`` resolves through
the installed wheel metadata, not through the .pth-derived sys.path.

When uv ships a release that drops the ``_editable_impl_`` prefix (or the
CPython hidden-pth behaviour relaxes), this file can be deleted.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
_WORKSPACE_SRC_DIRS = [
    _REPO_ROOT / "packages" / "core" / "src",
    _REPO_ROOT / "packages" / "runtime" / "src",
    _REPO_ROOT / "packages" / "api" / "src",
    _REPO_ROOT / "packages" / "voice" / "src",  # spec V1 T02 (D-V1-X-package-layout)
]

for _src in _WORKSPACE_SRC_DIRS:
    _src_str = str(_src)
    if _src.is_dir() and _src_str not in sys.path:
        sys.path.insert(0, _src_str)
