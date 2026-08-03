"""Health endpoint behaviour — specification section 21."""

from __future__ import annotations

import json

import pytest
from django.db import DatabaseError

from operations import health


def test_live_does_not_require_the_database(client):
    """Liveness must not touch the database.

    If it did, a database blip would make the orchestrator kill workers that are
    perfectly capable of serving. Note this test has no ``django_db`` marker: any
    database access would raise.
    """
    response = client.get("/health/live")

    assert response.status_code == 200
    assert json.loads(response.content) == {"status": "ok"}


@pytest.mark.django_db
def test_ready_reports_ok_when_database_and_extensions_are_present(client):
    response = client.get("/health/ready")

    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["status"] == "ok"
    assert payload["checks"] == {"database": "pass", "extensions": "pass"}


@pytest.mark.django_db
def test_ready_is_503_when_a_required_extension_is_missing(client, monkeypatch):
    """A database without btree_gist cannot enforce the overlap constraint.

    That failure is silent at the schema level — migrations that do not need the
    extension still apply — so readiness has to catch it. Simulated with a name that
    will never be installed, which keeps the test valid as later slices add real
    indexes that depend on the genuine extensions.
    """
    monkeypatch.setattr(
        health, "REQUIRED_EXTENSIONS", ("btree_gist", "citext", "pgcrypto", "not_installed_ext")
    )

    response = client.get("/health/ready")

    assert response.status_code == 503
    payload = json.loads(response.content)
    assert payload["status"] == "unavailable"
    assert payload["checks"]["extensions"] == "fail"
    assert "not_installed_ext" in payload["detail"]["extensions"]


@pytest.mark.django_db
def test_ready_is_503_when_the_database_is_unreachable(client, monkeypatch):
    def explode():
        raise DatabaseError("connection refused to db.internal as user vsat")

    monkeypatch.setattr(health.connection, "cursor", explode)

    response = client.get("/health/ready")

    assert response.status_code == 503
    payload = json.loads(response.content)
    assert payload["checks"] == {"database": "fail", "extensions": "fail"}


@pytest.mark.django_db
def test_ready_does_not_disclose_connection_details(client, monkeypatch):
    """Health endpoints are unauthenticated, so errors must not leak infrastructure.

    Specification section 21.15. The database error deliberately contains a hostname and
    a username; neither may reach the response body.
    """

    def explode():
        raise DatabaseError("connection refused to db.internal as user vsat password hunter2")

    monkeypatch.setattr(health.connection, "cursor", explode)

    body = client.get("/health/ready").content.decode()

    assert "db.internal" not in body
    assert "hunter2" not in body
    assert "vsat" not in body
    assert "DatabaseError" in body


@pytest.mark.django_db
def test_health_responses_are_not_cached(client):
    """A cached readiness response would report stale health."""
    for url in ("/health/live", "/health/ready"):
        cache_control = client.get(url).headers.get("Cache-Control", "")
        assert "no-cache" in cache_control or "max-age=0" in cache_control
