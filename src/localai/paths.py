"""Filesystem paths used by the Python localai package."""

from __future__ import annotations

import os
import re
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parents[1]

_INTERPOLATION = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?-([^}]*))?\}$")


def repo_path(*parts: str) -> Path:
    """Return a path inside the source checkout."""
    return REPO_ROOT.joinpath(*parts)


def compose_value(raw: str) -> str:
    """Resolve a compose ``${VAR:-default}`` literal the way compose would.

    The shipped compose file takes its default model from the installation's
    runtime configuration instead of being rewritten in place, so every reader
    that regex-reads ``DEFAULT_MODELS=`` must see the value compose would use.
    """
    match = _INTERPOLATION.match(raw.strip())
    if match is None:
        return raw.strip()
    return os.environ.get(match.group(1)) or (match.group(2) or "")
