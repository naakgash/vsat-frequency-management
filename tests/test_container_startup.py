"""What the container entrypoint asserts before serving.

`docker/entrypoint.sh` runs `set -eu`, so anything it invokes that exits non-zero takes
the container down at boot. The failure surfaces as
``init process is not running: failed precondition`` on the next `docker compose exec` —
a message that names neither the check nor the settings module involved.

That is exactly what happened: the entrypoint ran ``check --deploy --fail-level WARNING``
unconditionally, while compose starts the same image with ``config.settings.local``, where
DEBUG is on and the secure-cookie flags are off *by design*. The check asserted a posture
the settings module exists to switch off, and `make up` could never have worked.

These tests run the same checks the entrypoint runs, against the same settings modules, in
process. They need no Docker, which is the point — the Docker path is the one nobody
exercises until it breaks.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "docker" / "entrypoint.sh"

#: Enough to satisfy every required variable of `config.settings.base`, plus a secret key
#: long enough for `security.W009`. Values are throwaway: nothing connects to a database.
BASE_ENV = {
    "DJANGO_ALLOWED_HOSTS": "vsat.example.internal",
    "POSTGRES_DB": "probe",
    "POSTGRES_USER": "probe",
    "POSTGRES_PASSWORD": "probe",
}

#: Variables the test harness sets for its own purposes, which a container would never
#: have. They are dropped rather than inherited, because a check run against the harness's
#: environment answers a different question from the one these tests ask.
#:
#: ``DJANGO_ALLOW_ASYNC_UNSAFE`` is the one that matters: ``tests/ui/conftest.py`` must set
#: it in its module body — the session-scoped browser fixtures touch the ORM before any
#: function-scoped fixture could — so it is live for the entire pytest process. Inherited
#: here it produces ``async.E001``, which is a real deployment error being reported about
#: an environment that is not a deployment.
HARNESS_ONLY = ("DJANGO_ALLOW_ASYNC_UNSAFE", "DJANGO_DEBUG", "DJANGO_LOG_LEVEL")


def _run_check(settings_module: str, *flags: str) -> subprocess.CompletedProcess[str]:
    """Run ``manage.py check`` in a clean subprocess.

    A subprocess rather than ``call_command``: the settings module under test is not the
    one this suite is running under, and importing a second one into a configured process
    is not something Django supports.
    """
    environment = {
        **os.environ,
        **BASE_ENV,
        # Regenerated per call so a short key in a developer's .env cannot make the
        # production check fail for a reason unrelated to the code.
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
        "DJANGO_SETTINGS_MODULE": settings_module,
    }
    for name in HARNESS_ONLY:
        environment.pop(name, None)
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, flags are literals above
        [sys.executable, "manage.py", "check", *flags],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_deploy_check_passes_under_production_settings():
    """The assertion the entrypoint makes before serving production traffic.

    If this fails, a production container will not start — which is the correct
    behaviour, and the reason to find out here instead.
    """
    result = _run_check("config.settings.production", "--deploy", "--fail-level", "WARNING")

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_ordinary_check_passes_under_local_settings():
    """What the entrypoint runs for a development container."""
    result = _run_check("config.settings.local")

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_deploy_check_fails_under_local_settings():
    """Not a defect — the point.

    Local development is plain HTTP: DEBUG is on so tracebacks are readable, and the
    secure-cookie flags are off because a secure-only session cookie makes signing in
    over HTTP impossible. Running the production assertion against those settings is a
    category error, and this test states that plainly so nobody 'fixes' the entrypoint by
    putting the flag back.
    """
    result = _run_check("config.settings.local", "--deploy", "--fail-level", "WARNING")

    assert result.returncode != 0
    assert "W018" in result.stderr or "W018" in result.stdout


# ---------------------------------------------------------------------------
# The entrypoint script itself
# ---------------------------------------------------------------------------
def test_the_entrypoint_applies_the_deploy_check_only_to_production():
    """A text check, deliberately narrow: it pins the one line that caused the outage.

    The behavioural tests above establish *why* the branch has to exist; this one fails
    if the branch is removed and the flag applied unconditionally again.
    """
    script = ENTRYPOINT.read_text(encoding="utf-8")
    deploy_lines = [line for line in script.splitlines() if "--deploy" in line]

    assert deploy_lines, "the entrypoint no longer runs the deploy check at all"
    assert "*production*)" in script, (
        "the entrypoint runs --deploy without branching on DJANGO_SETTINGS_MODULE. "
        "Under development settings it exits 1 and, with `set -e`, kills the container "
        "at boot."
    )


def test_the_entrypoint_does_not_migrate():
    """Section 22.3 makes applying migrations a separate, reviewed step.

    Auto-migrating on container start applies a schema change to production the moment a
    container restarts — no review, no backup gate, and nothing in the release record.
    """
    script = ENTRYPOINT.read_text(encoding="utf-8")

    assert not re.search(r"manage\.py\s+migrate", script)


def test_the_entrypoint_execs_its_arguments():
    """``exec "$@"`` is what makes the application PID 1.

    Without it the real process is a child of the shell, and a stop signal reaches the
    shell rather than the server — so the container is killed on timeout instead of
    shutting down.
    """
    script = ENTRYPOINT.read_text(encoding="utf-8")

    assert 'exec "$@"' in script


def test_the_entrypoint_is_executable():
    assert os.access(ENTRYPOINT, os.X_OK), (
        "docker/entrypoint.sh has lost its executable bit; the container will fail to "
        "start with an exec format or permission error."
    )


# ---------------------------------------------------------------------------
# compose.yaml
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def compose_config() -> dict:
    yaml = pytest.importorskip("yaml", reason="PyYAML is not a project dependency")
    return yaml.safe_load((REPO_ROOT / "compose.yaml").read_text(encoding="utf-8"))


def test_the_development_stack_runs_development_settings(compose_config):
    """The image defaults to production settings; compose must override it.

    Without the override a developer's container asserts the production posture, fails
    the deploy check, and exits at boot.
    """
    web = compose_config["services"]["web"]

    assert web["environment"]["DJANGO_SETTINGS_MODULE"] == "config.settings.local"


def test_postgres_is_published_only_on_the_loopback_interface(compose_config):
    """Section 22.2. A bare "5432:5432" binds every interface on the host."""
    for port in compose_config["services"]["db"]["ports"]:
        assert str(port).startswith("127.0.0.1:"), port


def test_the_venv_is_an_anonymous_volume_over_the_bind_mount(compose_config):
    """The working tree is mounted over /app, so the image's venv needs protecting.

    Without the second entry the bind mount hides `/app/.venv` entirely and the container
    has no interpreter environment at all. With it, the venv survives — and acquires the
    lifetime problem the next test is about.
    """
    mounts = compose_config["services"]["web"]["volumes"]

    assert ".:/app" in mounts
    assert "/app/.venv" in mounts


def test_make_up_renews_anonymous_volumes():
    """The pairing that stops a stale venv shadowing a rebuilt image.

    An anonymous volume outlives `docker compose up`. So after a dependency is added, the
    rebuilt image contains the new package and the container mounts the *previous* venv
    over it — and the application dies at boot with a ModuleNotFoundError naming something
    that is plainly in the lock file, on a machine where the build just succeeded.

    That is not hypothetical: it happened when S17 added `pyotp` and `qrcode` to a stack
    that had been running since before them. `--renew-anon-volumes` costs a copy from the
    image and makes the venv always match the image it came from; the named
    `postgres_data` volume is unaffected, so the database survives.

    Asserted here rather than trusted because the flag looks removable — it is only
    necessary because of the mount in the test above, and the two are twenty lines apart
    in different files.

    **Comment lines are stripped before the assertion**, and that is not fussiness: the
    first version of this test matched the word in the explanatory comment directly above
    the command, so deleting the flag from the recipe left it passing. A guard rail that
    reads the documentation instead of the instruction guards nothing.
    """
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    up_target = makefile.split("\nup:")[1].split("\n.PHONY")[0]
    recipe = "\n".join(line for line in up_target.splitlines() if not line.strip().startswith("#"))

    assert "--renew-anon-volumes" in recipe, (
        "`make up` does not renew anonymous volumes. compose.yaml keeps /app/.venv as one, "
        "so without this a rebuilt image is shadowed by the venv from the previous run and "
        "the container exits at boot with a ModuleNotFoundError."
    )
