"""Environment variable access with fail-fast validation.

Specification section 21.8 requires secrets to live outside the repository. This module
is the only place environment variables are read, and it deliberately provides no
fallback for required values: a missing secret must stop the process at boot rather
than silently starting with a development default.
"""

from __future__ import annotations

import os


class ImproperlyConfigured(Exception):
    """Raised at import time when a required environment variable is absent."""


def require(name: str) -> str:
    """Return a required environment variable, or fail loudly.

    There is intentionally no ``default`` parameter. A value that may be defaulted is
    not a secret, and belongs in :func:`optional`.
    """
    try:
        value = os.environ[name]
    except KeyError:
        raise ImproperlyConfigured(
            f"Required environment variable {name!r} is not set. "
            f"See .env.example for the full list."
        ) from None
    if not value.strip():
        raise ImproperlyConfigured(f"Required environment variable {name!r} is empty.")
    return value


def optional(name: str, default: str) -> str:
    """Return an environment variable that has a safe non-secret default."""
    return os.environ.get(name) or default


def boolean(name: str, default: bool) -> bool:
    """Parse a boolean environment variable."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(f"Environment variable {name!r} must be a boolean, got {raw!r}.")


def csv_list(name: str, default: str) -> list[str]:
    """Parse a comma-separated environment variable into a list of trimmed values."""
    raw = optional(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]
