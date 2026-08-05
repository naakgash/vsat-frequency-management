"""Shared test fixtures and helpers.

Tests run against a real PostgreSQL cluster. See config/settings/test.py for why there
is no SQLite path.
"""

from __future__ import annotations

import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone

from accounts.constants import Role
from accounts.models import UserBeamScope, UserHubScope
from beams import services as beam_services
from satnet_paths import services
from satnet_paths.constants import InputMode, PathStatus
from satnets import services as satnet_services
from tests.factories import make_admin, make_user
from tests.inventory.factories import make_gateway, make_hub
from tests.spectrum.factories import make_entitlement

REPO_ROOT = Path(__file__).resolve().parent.parent

# Directories that are never part of the product surface: third-party code, build
# output, virtual environments and version-control metadata.
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".hypothesis",
    "__pycache__",
    "staticfiles",
    "node_modules",
    "static/vendor",
}


def _is_excluded(path: Path) -> bool:
    relative = path.relative_to(REPO_ROOT).as_posix()
    if any(relative.startswith(f"{prefix}/") or relative == prefix for prefix in EXCLUDED_DIRS):
        return True
    return any(part in EXCLUDED_DIRS for part in path.relative_to(REPO_ROOT).parts)


def tracked_files(*suffixes: str) -> list[Path]:
    """Return in-repository source files with any of the given suffixes.

    Driven by git rather than a directory walk so that build output, virtual
    environments and other ignored artefacts can never influence a guard-rail test.

    ``--others --exclude-standard`` includes files that are new but not yet staged. That
    matters: a guard rail that only inspects committed files would let a template
    containing a forbidden term pass locally and fail only after it was committed, which
    is precisely when it is most annoying to fix.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    files = []
    for name in result.stdout.split("\0"):
        if not name:
            continue
        path = REPO_ROOT / name
        if not path.is_file() or _is_excluded(path):
            continue
        if suffixes and path.suffix not in suffixes:
            continue
        files.append(path)
    return files


@pytest.fixture
def anonymous_client(client):
    """Django test client with no authenticated session."""
    return client


@pytest.fixture
def seeded_roles(db):
    """Ensure the four role groups exist with their capabilities.

    Migrations seed them, but ``django_db(transaction=True)`` flushes every table after
    each test — including the migration-seeded rows. Without this a transactional test
    that runs second finds no groups at all, so ``groups.set()`` silently assigns
    nothing and an authorization test passes for entirely the wrong reason.
    """
    from django.apps import apps as django_apps

    from accounts.seeding import apply_capability_matrix

    apply_capability_matrix(django_apps)


@pytest.fixture
def seeded_dictionary(db):
    """Ensure the Specification Dictionary is populated.

    Same reason as :func:`seeded_roles`: an accessibility test against an empty
    dictionary would render no information buttons and pass vacuously.
    """
    from specifications.migrations import _seed_helper

    _seed_helper.ensure_seeded()


# ---------------------------------------------------------------------------
# The §15.2 lifecycle world — shared because approvals and Satnet Paths both need it
#
# The lifecycle is the first part of the product where *who you are* changes what happens,
# so these build an Operator and an Approver with real grants rather than doing everything
# as an administrator. An administrator would pass most of these tests and prove nothing:
# the point of the graph's split is that whoever plans a transmission does not put it on air.
# ---------------------------------------------------------------------------

MHZ = 1_000_000


@pytest.fixture
def lifecycle_world(db) -> dict[str, Any]:
    setup = make_entitlement(code="LC", start_hz=0, end_hz=100 * MHZ)
    admin = make_admin("lc-admin")

    beam_services.validate_beam(actor=admin, beam=setup.beam)
    setup.beam.refresh_from_db()
    beam_services.set_active(actor=admin, beam=setup.beam, active=True)

    hub = make_hub(make_gateway("GW-LC"), "HUB-LC")
    satnet = satnet_services.create(
        actor=admin,
        values={
            "code": "SN-LC",
            "name": "Lifecycle",
            "beam": setup.beam,
            "hub": hub,
            "effective_from": timezone.now() - timezone.timedelta(days=1),
        },
    )

    operator = make_user("lc-operator", roles=[Role.OPERATOR])
    approver = make_user("lc-approver", roles=[Role.APPROVER])
    for user in (operator, approver):
        UserBeamScope.objects.create(user=user, beam=setup.beam)
        UserHubScope.objects.create(user=user, hub=hub)

    return {
        "setup": setup,
        "satnet": satnet,
        "admin": admin,
        "operator": operator,
        "approver": approver,
        "hub": hub,
    }


@pytest.fixture
def make_path(lifecycle_world):
    """Create a Satnet Path in any status, by walking the graph rather than by writing it.

    Deliberately not a direct row write: a fixture that set ``status="ON_AIR"`` would produce a
    record whose reservations do not exist, and every test built on it would be testing a state
    the product cannot reach.
    """
    from satnet_paths import lifecycle

    def build(
        status: str = PathStatus.DRAFT,
        *,
        code: str = "LC-1",
        centre: int = 50 * MHZ,
        actor: Any = None,
    ):
        world = lifecycle_world
        path = services.create(
            actor=actor or world["operator"],
            satnet=world["satnet"],
            values={
                "code": code,
                "direction": "FWD",
                "status": PathStatus.DRAFT,
                "input_mode": InputMode.OCCUPIED_BW,
                "input_value": 10 * MHZ,
                "rolloff": Decimal("0.2"),
                "canonical_center_hz": centre,
                "valid_from": timezone.now(),
            },
        )
        route = {
            PathStatus.DRAFT: [],
            PathStatus.PLANNED: ["plan"],
            PathStatus.PENDING_APPROVAL: ["plan", "submit"],
            PathStatus.ON_AIR: ["plan", "submit", "approve"],
            PathStatus.SUSPENDED: ["plan", "submit", "approve", "suspend"],
        }[status]

        for action in route:
            if action == "approve":
                from approvals import services as approval_services
                from approvals.constants import Decision

                approval_services.decide(
                    actor=world["approver"], path=path, decision=Decision.APPROVED
                )
            else:
                lifecycle.transition(
                    actor=world["operator"] if action in {"plan", "submit"} else world["approver"],
                    path=path,
                    action=action,
                )
        return path

    return build
