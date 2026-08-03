"""Production settings — Ubuntu Server 24.04, behind nginx, TLS terminated at nginx.

Every value here is driven by specification section 21. DEBUG is not merely defaulted
to False, it is hard-coded: there is no environment variable that can turn it on.
"""

from __future__ import annotations

from config import env

from .base import *

# Not configurable. A production deployment with DEBUG on would leak stack traces and
# settings, so the switch is removed rather than defaulted.
DEBUG = False

# No default: a production host list must be stated explicitly.
ALLOWED_HOSTS = env.csv_list("DJANGO_ALLOWED_HOSTS", "")
if not ALLOWED_HOSTS:
    raise env.ImproperlyConfigured(
        "DJANGO_ALLOWED_HOSTS must list the hostnames this deployment serves."
    )

CSRF_TRUSTED_ORIGINS = env.csv_list("DJANGO_CSRF_TRUSTED_ORIGINS", "")

# --- TLS and cookies (21.1, 21.2) ------------------------------------------
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = False  # HTMX reads the token from the cookie to set the header.

# nginx terminates TLS and forwards the original scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SECURE_HSTS_SECONDS = int(env.optional("DJANGO_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# --- Uploads (21.10) --------------------------------------------------------
# Import workbooks are the only upload path. nginx enforces the same ceiling so an
# oversized body is rejected before it reaches a worker.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(env.optional("DJANGO_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = DATA_UPLOAD_MAX_MEMORY_SIZE
DATA_UPLOAD_MAX_NUMBER_FIELDS = 2000

# --- Static -----------------------------------------------------------------
# nginx serves STATIC_ROOT directly; collectstatic runs during the release flow.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"},
}
