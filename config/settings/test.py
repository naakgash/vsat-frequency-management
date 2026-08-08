"""Test settings.

Runs against a real PostgreSQL cluster. There is deliberately no SQLite option: the
exclusion constraints, int8range columns and btree_gist indexes that make up the final
defence layer (specification section 8.3) do not exist in SQLite, so a green SQLite
suite would prove nothing about the behaviour that matters most.
"""

from __future__ import annotations

import os

# Defaults let `pytest` run with no environment set up at all, while still allowing CI
# to point at a different cluster. These are test-only credentials for a throwaway
# database, not secrets.
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-key-not-used-in-any-real-environment")
os.environ.setdefault("POSTGRES_DB", "vsat_dev")
os.environ.setdefault("POSTGRES_USER", "vsat")
os.environ.setdefault("POSTGRES_PASSWORD", "vsat_local_dev")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5432")

from .base import *

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Fast, deterministic password hashing. Never used outside the test settings module.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Keep test output readable; the logging configuration itself is exercised by its own
# tests rather than by every unrelated test emitting records.
LOGGING["root"]["level"] = "CRITICAL"

# ---------------------------------------------------------------------------
# Second factor (section 21)
# ---------------------------------------------------------------------------
# **Off by default here, and on in production.** Almost every test in this suite signs in as
# an administrator to exercise something that has nothing to do with authentication, and the
# middleware correctly refuses an un-enrolled administrator every page — so leaving this on
# would mean enrolling a second factor before testing a frequency calculation.
#
# The cost is that the production value is not exercised by the bulk of the suite, so it is
# exercised deliberately instead: tests/accounts/test_mfa.py turns it back on with
# `override_settings` and asserts the whole flow, and one test there asserts that
# `config.settings.base` still requires it for administrators — so switching it off *here*
# can never quietly become switching it off everywhere.
MFA_REQUIRED_ROLES = ()
MFA_REQUIRED_FOR_ALL = False
