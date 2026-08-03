"""Create the four demo accounts of specification section 22.1.

Deliberately a management command rather than a data migration. A migration would run in
every environment, creating accounts with known passwords in production. This command
refuses to run unless ``DEBUG`` is on, and that refusal can only be overridden by an
explicit flag that names what it is doing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.constants import Role
from accounts.models import User

DEMO_PASSWORD = "demo-password-change-me"  # noqa: S105 - development fixture only


@dataclass(frozen=True)
class DemoUser:
    """One demo account. A dataclass rather than a dict so the role list and the model
    field values stay separate — they are passed to different calls."""

    username: str
    full_name: str
    job_title: str
    roles: tuple[str, ...]

    @property
    def email(self) -> str:
        return f"{self.username}@example.invalid"

    def field_values(self) -> dict[str, str]:
        return {
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "job_title": self.job_title,
        }


DEMO_USERS = [
    DemoUser("admin", "Demo Administrator", "System administrator", (Role.ADMIN,)),
    DemoUser("operator", "Demo Operator", "Spectrum planning operator", (Role.OPERATOR,)),
    DemoUser("approver", "Demo Approver", "RF engineering approver", (Role.APPROVER,)),
    DemoUser("observer", "Demo Observer", "Network operations observer", (Role.OBSERVER,)),
]


class Command(BaseCommand):
    help = "Create the four demo user accounts for local development."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--i-understand-this-creates-known-passwords",
            action="store_true",
            dest="acknowledged",
            help=(
                "Required to run when DEBUG is off. Creates accounts whose passwords are "
                "published in source control."
            ),
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEBUG and not options["acknowledged"]:
            raise CommandError(
                "Refusing to create demo accounts with known passwords while DEBUG is off. "
                "These accounts are for local development only. If you genuinely intend "
                "this, pass --i-understand-this-creates-known-passwords."
            )

        groups = {group.name: group for group in Group.objects.filter(name__in=Role.values)}
        missing = sorted(set(Role.values) - set(groups))
        if missing:
            raise CommandError(
                f"Role groups are missing: {', '.join(missing)}. Run `manage.py migrate` first."
            )

        created, updated = 0, 0
        for spec in DEMO_USERS:
            user, was_created = User.objects.get_or_create(
                username=spec.username, defaults=spec.field_values()
            )
            if was_created:
                created += 1
            else:
                updated += 1
            # Reset the password on every run so a local environment is always usable.
            user.set_password(DEMO_PASSWORD)
            user.save()
            user.groups.set([groups[role] for role in spec.roles])

        self.stdout.write(
            self.style.SUCCESS(
                f"Demo accounts ready: {created} created, {updated} updated.\n"
                f"Usernames: {', '.join(spec.username for spec in DEMO_USERS)}\n"
                f"Password for all: {DEMO_PASSWORD}"
            )
        )
