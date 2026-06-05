"""Security helpers (SDAD §4.1).

Implements the directory-traversal protection requirement. Secret management
(AWS Secrets Manager / HashiCorp Vault) and PDF sandboxing (Docker + nobody +
AppArmor) are deployment-level concerns documented in the README; here we
provide the in-process pieces.
"""
from __future__ import annotations

import os

try:
    from werkzeug.utils import secure_filename as _werkzeug_secure_filename
except ImportError:  # pragma: no cover - werkzeug optional at import time
    _werkzeug_secure_filename = None


def safe_filename(filename: str) -> str:
    """Sanitise a user/remote-supplied filename, stripping path components.

    Prevents path-injection such as ``../../../etc/passwd``. Prefers
    ``werkzeug.utils.secure_filename`` (per the SDAD) and falls back to a
    conservative built-in if werkzeug is unavailable.
    """
    if _werkzeug_secure_filename is not None:
        cleaned = _werkzeug_secure_filename(filename)
        return cleaned or "unnamed"

    # Minimal fallback: keep only the basename and an allowlist of characters.
    base = os.path.basename(filename).replace("..", "")
    cleaned = "".join(c for c in base if c.isalnum() or c in "._- ").strip()
    return cleaned.replace(" ", "_") or "unnamed"


def safe_join(base_dir: str, *paths: str) -> str:
    """Join paths and guarantee the result stays inside ``base_dir``.

    Raises ValueError if the resolved path would escape the storage root —
    a second line of defence behind :func:`safe_filename`.
    """
    base_abs = os.path.abspath(base_dir)
    candidate = os.path.abspath(os.path.join(base_abs, *paths))
    if candidate != base_abs and not candidate.startswith(base_abs + os.sep):
        raise ValueError(f"Path traversal attempt blocked: {paths!r}")
    return candidate
