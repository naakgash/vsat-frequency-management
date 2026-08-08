"""The deployment's security posture, asserted rather than described. §21, §22.2.

Everything here reads a file or a settings module and checks a property. That is deliberately
unglamorous: the requirements in §21 and §22.2 are the kind that are written down once, drift
during an unrelated change, and are noticed by somebody scanning the host from outside.

The compose assertions are the sharpest ones. "PostgreSQL is unreachable from outside the
compose network" is one `ports:` line away from being false, and the line is easy to add during
a debugging session and easy to forget to remove.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_COMPOSE = ROOT / "compose.production.yaml"
NGINX_CONF = ROOT / "docker/nginx/vsat.conf"


@pytest.fixture(scope="module")
def production():
    """The production settings module, imported with the one value it refuses to default.

    It raises without `DJANGO_ALLOWED_HOSTS` — which is itself a property asserted below, so
    supplying it here is not working around the behaviour, it is the only way to read the rest
    of the module at all.
    """
    import importlib
    import os

    had = os.environ.get("DJANGO_ALLOWED_HOSTS")
    os.environ["DJANGO_ALLOWED_HOSTS"] = "vsat.example.test"
    try:
        module = importlib.import_module("config.settings.production")
        yield importlib.reload(module)
    finally:
        if had is None:
            os.environ.pop("DJANGO_ALLOWED_HOSTS", None)
        else:
            os.environ["DJANGO_ALLOWED_HOSTS"] = had


@pytest.fixture(scope="module")
def compose() -> dict:
    import yaml

    return yaml.safe_load(PRODUCTION_COMPOSE.read_text())


# ---------------------------------------------------------------------------
# §22.2 — what is reachable from outside
# ---------------------------------------------------------------------------
def test_postgres_publishes_no_port(compose):
    """§22.2. One `ports:` line away from being false, which is why it is a test."""
    assert "ports" not in compose["services"]["db"], (
        "The production database publishes a port. §22.2 requires PostgreSQL to be "
        "unreachable from outside the compose network; the development compose file "
        "publishes 5432 on the loopback and this one deliberately does not."
    )


def test_the_application_publishes_no_port(compose):
    """Gunicorn is reached through nginx and nowhere else."""
    assert "ports" not in compose["services"]["web"]


def test_only_nginx_publishes_and_only_80_and_443(compose):
    publishing = {
        name: service.get("ports")
        for name, service in compose["services"].items()
        if service.get("ports")
    }

    assert set(publishing) == {"nginx"}
    assert {port.split(":")[0] for port in publishing["nginx"]} == {"80", "443"}


def test_the_internal_network_is_internal(compose):
    """`internal: true` is what stops a container reaching the outside world, and what stops
    the outside world reaching it."""
    assert compose["networks"]["internal"]["internal"] is True


def test_only_nginx_is_on_the_public_network(compose):
    on_public = [
        name
        for name, service in compose["services"].items()
        if "public" in (service.get("networks") or [])
    ]

    assert on_public == ["nginx"]


def test_the_image_is_a_tag_rather_than_a_build(compose):
    """What runs is what CI built and scanned, not what happened to be on the host."""
    web = compose["services"]["web"]

    assert "build" not in web
    assert web["image"].startswith("${VSAT_IMAGE")


def test_no_source_is_bind_mounted_into_the_application(compose):
    """The development file mounts the working tree; a production image contains a copy."""
    mounts = compose["services"]["web"].get("volumes") or []

    assert not [mount for mount in mounts if mount.startswith((".:", "./"))]


def test_migrations_are_not_run_by_the_container(compose):
    """§22.3 makes applying them a separate, reviewed step. A container that migrates on start
    applies a schema change the moment it restarts, with nobody watching."""
    command = " ".join(compose["services"]["web"]["command"])
    entrypoint = (ROOT / "docker/entrypoint.sh").read_text()

    assert "migrate" not in command
    assert "manage.py migrate" not in entrypoint


# ---------------------------------------------------------------------------
# §21 — TLS, headers and rate limits
# ---------------------------------------------------------------------------
def test_port_80_only_redirects():
    conf = NGINX_CONF.read_text()
    plain = conf.split("listen 443")[0]

    assert "return 301 https://" in plain


def test_tls_is_modern_only():
    """No TLS 1.0 or 1.1, which are the versions a scanner reports and a compliance review
    asks about."""
    conf = NGINX_CONF.read_text()

    assert "ssl_protocols       TLSv1.2 TLSv1.3;" in conf
    assert "TLSv1.1" not in conf


def test_hsts_is_set_by_nginx_as_well_as_django(production):
    """Duplicated on purpose: the header then survives a request that never reaches the
    application — a 502, or a static file."""
    assert "Strict-Transport-Security" in NGINX_CONF.read_text()
    assert production.SECURE_HSTS_SECONDS >= 31_536_000
    assert production.SECURE_HSTS_INCLUDE_SUBDOMAINS


def test_the_sign_in_form_has_its_own_tighter_rate_limit():
    """The second line, not the first: `accounts.services` already throttles per account. This
    one bounds the attempt rate per source, which an account-scoped throttle cannot — spraying
    one password across four hundred usernames never trips it."""
    conf = NGINX_CONF.read_text()

    assert "limit_req_zone $binary_remote_addr zone=signin" in conf
    assert "limit_req zone=signin" in conf

    signin_rate = re.search(r"zone=signin:\d+m rate=(\d+)r/m", conf)
    assert signin_rate and int(signin_rate.group(1)) <= 30


def test_a_rate_limited_client_is_told_so():
    """429, not nginx's default 503. "The server is broken" sends people to the wrong place."""
    assert "limit_req_status 429;" in NGINX_CONF.read_text()


def test_the_upload_ceiling_matches_at_both_layers(production):
    """An oversized body is refused by nginx rather than after occupying a worker."""
    assert "client_max_body_size 25m;" in NGINX_CONF.read_text()
    assert production.DATA_UPLOAD_MAX_MEMORY_SIZE == 25 * 1024 * 1024


def test_the_server_version_is_not_advertised():
    assert "server_tokens off;" in NGINX_CONF.read_text()


# ---------------------------------------------------------------------------
# §21 — the settings that cannot be switched off
# ---------------------------------------------------------------------------
def test_debug_is_not_configurable_in_production():
    """Hard-coded, not defaulted. A production deployment with DEBUG on leaks stack traces and
    settings, so the switch is removed rather than given a safe default."""
    source = (ROOT / "config/settings/production.py").read_text()

    assert "DEBUG = False" in source
    assert "DJANGO_DEBUG" not in source


def test_secure_cookies_and_ssl_redirect_are_on(production):
    assert production.SECURE_SSL_REDIRECT
    assert production.SESSION_COOKIE_SECURE
    assert production.CSRF_COOKIE_SECURE


def test_a_production_deployment_must_name_its_hosts(monkeypatch):
    """No default: a host list that defaulted to something would default to something wrong.

    Asserted by importing the module *without* the variable and watching it refuse, rather
    than by grepping for the `raise` — the behaviour is what matters, and a `raise` that some
    later refactor made unreachable would still match a grep.
    """
    import importlib

    from config import env

    monkeypatch.delenv("DJANGO_ALLOWED_HOSTS", raising=False)

    with pytest.raises(env.ImproperlyConfigured):
        importlib.reload(importlib.import_module("config.settings.production"))


def test_every_log_line_carries_the_request_id():
    """§21.14. It is the same value `audit_event.request_id` holds, so a stack trace leads to
    exactly the events that request produced."""
    from django.conf import settings

    assert "%(request_id)s" in settings.LOGGING["formatters"]["verbose"]["format"]
    assert settings.LOGGING["filters"]["request_id"]["()"] == "audit.context.RequestIdFilter"


def test_the_security_logger_is_not_turned_down_with_everything_else():
    """Turning logging down must never turn the security trail off."""
    from config.settings import base

    assert base.LOGGING["loggers"]["django.security"]["level"] == "INFO"
    assert base.LOGGING["loggers"]["django.security"]["propagate"] is False
