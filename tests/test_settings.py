"""Configuration safety.

Specification sections 21.8 and 21.15. These assert properties of the settings modules
themselves, which is the only place a production misconfiguration can be caught before
it reaches production.
"""

from __future__ import annotations

import importlib

import pytest
from django.conf import settings

from config import env


def test_secret_key_has_no_source_code_default(monkeypatch):
    """A required secret must have no fallback (section 21.8)."""
    monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)

    with pytest.raises(env.ImproperlyConfigured) as excinfo:
        env.require("DJANGO_SECRET_KEY")

    assert "DJANGO_SECRET_KEY" in str(excinfo.value)


def test_required_value_rejects_whitespace(monkeypatch):
    monkeypatch.setenv("DJANGO_SECRET_KEY", "   ")

    with pytest.raises(env.ImproperlyConfigured):
        env.require("DJANGO_SECRET_KEY")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("No", False),
        ("off", False),
    ],
)
def test_boolean_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("PROBE_FLAG", raw)

    assert env.boolean("PROBE_FLAG", default=not expected) is expected


def test_boolean_rejects_nonsense(monkeypatch):
    """Silently treating 'maybe' as False is how a security flag ends up off."""
    monkeypatch.setenv("PROBE_FLAG", "maybe")

    with pytest.raises(env.ImproperlyConfigured):
        env.boolean("PROBE_FLAG", default=False)


def test_production_debug_cannot_be_enabled_by_environment(monkeypatch):
    """DEBUG is hard-coded off in production, not merely defaulted off.

    An environment variable that can turn DEBUG on in production is a stack-trace and
    settings disclosure waiting to happen (section 21.15).
    """
    monkeypatch.setenv("DJANGO_DEBUG", "true")
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "vsat.example.internal")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "probe-key-for-import-only")
    monkeypatch.setenv("POSTGRES_DB", "probe")
    monkeypatch.setenv("POSTGRES_USER", "probe")
    monkeypatch.setenv("POSTGRES_PASSWORD", "probe")

    production = importlib.import_module("config.settings.production")
    importlib.reload(production)

    assert production.DEBUG is False
    assert production.SESSION_COOKIE_SECURE is True
    assert production.CSRF_COOKIE_SECURE is True
    assert production.SECURE_SSL_REDIRECT is True
    assert production.SECURE_HSTS_SECONDS > 0


def test_production_requires_an_explicit_allowed_hosts(monkeypatch):
    monkeypatch.setenv("DJANGO_ALLOWED_HOSTS", "")
    monkeypatch.setenv("DJANGO_SECRET_KEY", "probe-key-for-import-only")
    monkeypatch.setenv("POSTGRES_DB", "probe")
    monkeypatch.setenv("POSTGRES_USER", "probe")
    monkeypatch.setenv("POSTGRES_PASSWORD", "probe")

    production = importlib.import_module("config.settings.production")

    with pytest.raises(env.ImproperlyConfigured):
        importlib.reload(production)


def test_the_database_backend_is_postgresql():
    """There is no SQLite path in this project; the constraints do not exist there."""
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"


def test_timestamps_are_stored_in_utc():
    """Specification section 14.1."""
    assert settings.USE_TZ is True
    assert settings.TIME_ZONE == "UTC"


def test_session_cookies_are_hardened():
    assert settings.SESSION_COOKIE_HTTPONLY is True
    assert settings.SESSION_COOKIE_SAMESITE == "Lax"
    assert settings.X_FRAME_OPTIONS == "DENY"
    assert settings.SECURE_CONTENT_TYPE_NOSNIFF is True
