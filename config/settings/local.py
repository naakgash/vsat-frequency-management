"""Local development settings — docker compose or a native PostgreSQL cluster."""

from __future__ import annotations

from config import env

from .base import *

DEBUG = env.boolean("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env.csv_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")

# Development is plain HTTP, so the secure-cookie flags of production would prevent
# logging in at all. Production overrides these back on; see settings/production.py.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

INTERNAL_IPS = ["127.0.0.1"]
