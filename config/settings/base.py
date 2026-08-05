"""Settings shared by every environment.

Environment-specific modules (local, test, production) import everything from here and
override only what genuinely differs. Nothing in this file may contain a secret or a
production hostname.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config import env

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env.require("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS: list[str] = env.csv_list("DJANGO_ALLOWED_HOSTS", "")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Provides the range fields, ExclusionConstraint and extension operations that the
    # spectrum reservation model depends on (docs/design/04).
    "django.contrib.postgres",
    # Local modules. Order follows the dependency direction in
    # docs/design/01-repository-structure.md section 1: cross-cutting modules first.
    "audit",
    "accounts",
    "calculations",
    "specifications",
    "inventory",
    "beams",
    "spectrum",
    "satnets",
    "satnet_paths",
    "approvals",
    "reporting",
    "operations",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # After authentication so the actor is known, before anything that may audit.
    "audit.middleware.RequestContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
#
# PostgreSQL only. There is no SQLite fallback anywhere in this project: exclusion
# constraints, int8range and btree_gist do not exist there, and they are the final
# defence layer described in specification section 8.3.
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env.require("POSTGRES_DB"),
        "USER": env.require("POSTGRES_USER"),
        "PASSWORD": env.require("POSTGRES_PASSWORD"),
        "HOST": env.optional("POSTGRES_HOST", "localhost"),
        "PORT": env.optional("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(env.optional("POSTGRES_CONN_MAX_AGE", "60")),
        "OPTIONS": {
            # Fail fast rather than hanging a worker on an unreachable database.
            "connect_timeout": 5,
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Authentication
#
# OQ-16 (local vs LDAP/AD) is unresolved. The backend list is the extension point;
# local authentication is the only one implemented in the MVP.
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internationalisation
#
# Specification section 1: the complete application is English. USE_I18N stays off
# because there is no second language to switch to, and leaving it on invites
# half-translated strings.
#
# UTC is the authoritative operational *and* display time zone — the OQ-23 answer, recorded
# as A-28 and ADR-0022. TIME_ZONE is therefore not a display preference here: it is the zone
# every validity check, overlap calculation and audit record is computed in, which is why
# nothing activates a per-request zone. A local zone may later be offered as a *secondary*
# display, and the way to add it is another column on the screen — never
# `timezone.activate()`, which would reach the forms that parse an operator's input.
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files
#
# Everything is vendored under static/. Specification section 19.4 rules out
# CDN-only dependencies, so no template may reference an external host.
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# ---------------------------------------------------------------------------
# Security defaults (section 21). Production tightens these further.
# ---------------------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# --- Login rate limiting and temporary lockout (sections 21.4, 21.5) --------
# Rolling window: the lockout expires as failures age out rather than requiring an
# administrator to intervene, so a password-spray cannot become a denial of service
# against real operators.
# Two independent thresholds. Per username is strict, and stops a targeted attack
# that rotates source addresses. Per source address is far looser, and stops password
# spraying across many accounts; it must stay high because operators routinely share an
# address behind NAT or a VPN concentrator, where a low limit would let one mistyped
# password lock out an entire site.
LOGIN_FAILURE_LIMIT = int(env.optional("LOGIN_FAILURE_LIMIT", "5"))
LOGIN_IP_FAILURE_LIMIT = int(env.optional("LOGIN_IP_FAILURE_LIMIT", "50"))
LOGIN_LOCKOUT_SECONDS = int(env.optional("LOGIN_LOCKOUT_SECONDS", "900"))

# ---------------------------------------------------------------------------
# Allocation lifecycle policy (§15.2, §15.3)
#
# Two open questions whose provisional positions are settings rather than rules, so that
# answering either is a configuration change and not a migration.
#
# `docs/design/02` §8 sketches a `SystemSetting` table read through
# `operations.settings.get()`. These are Django settings instead, and deliberately: the
# readers sit *below* `operations` in the module layering, so a database-backed store there
# would be unreachable from the code that needs it without inverting the dependency. When a
# settings screen arrives it can back these with a table and keep the same two names.
# ---------------------------------------------------------------------------
# **OQ-08**, ADR-0017. Does a SUSPENDED allocation keep its spectrum? §15.3 recommends
# retaining, and it is the safer error: releasing means a suspension can silently become
# unresumable when somebody else takes the gap.
SUSPENDED_RETAINS_SPECTRUM = env.optional("SUSPENDED_RETAINS_SPECTRUM", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# **OQ-11**, §12. Must the approver be somebody other than the author? Default true —
# separation of duties is the point of having an Approver role at all, and a platform that
# let one person plan and approve their own transmission would make the role decorative.
REQUIRE_SEPARATE_APPROVER = env.optional("REQUIRE_SEPARATE_APPROVER", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# ---------------------------------------------------------------------------
# Logging — structured, no stack traces to the user (section 21.14, 21.15)
# ---------------------------------------------------------------------------
# Annotated so environment-specific modules can override nested keys without mypy
# inferring the value type as `object`.
LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s %(levelname)s %(name)s %(process)d %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": env.optional("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "django.security": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
