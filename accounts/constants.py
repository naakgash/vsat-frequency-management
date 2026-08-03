"""Role names and capability codenames.

Roles are the four of specification section 12. They are implemented as Django groups so
that role membership is ordinary, auditable data rather than a hard-coded attribute on
the user row — an operator who is also an approver is a real situation, and a single
``role`` column would not express it.
"""

from __future__ import annotations

from django.db import models


class Role(models.TextChoices):
    """The four roles of specification section 12."""

    ADMIN = "admin", "Administrator"
    OPERATOR = "operator", "Operator"
    APPROVER = "approver", "Approver"
    OBSERVER = "observer", "Observer"


#: Roles ordered from most to least privileged, for display only. Authorization never
#: consults this ordering — roles are additive, not hierarchical, and an Approver is not
#: "more" than an Operator, merely different.
ROLE_DISPLAY_ORDER = [Role.ADMIN, Role.OPERATOR, Role.APPROVER, Role.OBSERVER]


# ---------------------------------------------------------------------------
# Capability codenames delivered by this slice.
#
# The full target list is in docs/design/03 section 2.2. Codenames appear here as the
# slice that implements them lands, so the seeded role groups never reference a
# permission that does not exist.
# ---------------------------------------------------------------------------
MANAGE_USERS = "accounts.manage_users"
MANAGE_SCOPES = "accounts.manage_scopes"
VIEW_AUDIT = "audit.view_auditevent"
VIEW_ALL_AUDIT = "audit.view_all_auditevent"

#: Capability -> roles that hold it. Seeded into the role groups by a data migration and
#: asserted, cell by cell, by tests/permissions/test_matrix.py.
CAPABILITY_MATRIX: dict[str, tuple[str, ...]] = {
    MANAGE_USERS: (Role.ADMIN,),
    MANAGE_SCOPES: (Role.ADMIN,),
    VIEW_ALL_AUDIT: (Role.ADMIN,),
    VIEW_AUDIT: (Role.ADMIN, Role.OPERATOR, Role.APPROVER),
}
