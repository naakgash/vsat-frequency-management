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

# Capabilities belonging to modules above accounts in the dependency graph are named as
# plain strings, never imported. That is what keeps accounts free of domain imports while
# still holding the single seeded definition of what each role may do.
VIEW_SPECIFICATION = "specifications.view_specificationdefinition"
CHANGE_SPECIFICATION = "specifications.change_specificationdefinition"

VIEW_SATELLITE = "inventory.view_satellite"
VIEW_BAND = "inventory.view_band"
VIEW_GATEWAY = "inventory.view_gateway"
VIEW_HUB = "inventory.view_hub"
VIEW_EQUIPMENT = "inventory.view_equipmentprofile"
VIEW_GUARD_POLICY = "inventory.view_guardpolicy"
VIEW_FREQUENCY_WINDOW = "inventory.view_frequencywindow"
VIEW_PAYLOAD_PATH = "inventory.view_payloadpath"
VIEW_SPECTRUM_RESOURCE = "inventory.view_spectrumresource"
MANAGE_INVENTORY = "inventory.manage_inventory"

VIEW_BEAM = "beams.view_beam"
MANAGE_BEAMS = "beams.manage_beams"

#: Read access to inventory is uniform across the five entities.
_ALL_ROLES = (Role.ADMIN, Role.OPERATOR, Role.APPROVER, Role.OBSERVER)

#: Capability -> roles that hold it. Seeded into the role groups by a data migration and
#: asserted, cell by cell, by tests/permissions/test_matrix.py.
CAPABILITY_MATRIX: dict[str, tuple[str, ...]] = {
    MANAGE_USERS: (Role.ADMIN,),
    MANAGE_SCOPES: (Role.ADMIN,),
    VIEW_ALL_AUDIT: (Role.ADMIN,),
    VIEW_AUDIT: (Role.ADMIN, Role.OPERATOR, Role.APPROVER),
    # Every role reads the dictionary — an Operator needs to look up what a code means.
    # Only an administrator edits it (specification section 12, acceptance criterion 26.2).
    VIEW_SPECIFICATION: (Role.ADMIN, Role.OPERATOR, Role.APPROVER, Role.OBSERVER),
    CHANGE_SPECIFICATION: (Role.ADMIN,),
    # Inventory is readable by every role — an Operator selecting a Beam needs to see the
    # Satellite and Band behind it. Only an administrator may create or change master
    # data (specification section 12).
    VIEW_SATELLITE: _ALL_ROLES,
    VIEW_BAND: _ALL_ROLES,
    VIEW_GATEWAY: _ALL_ROLES,
    VIEW_HUB: _ALL_ROLES,
    VIEW_EQUIPMENT: _ALL_ROLES,
    VIEW_GUARD_POLICY: _ALL_ROLES,
    VIEW_FREQUENCY_WINDOW: _ALL_ROLES,
    VIEW_PAYLOAD_PATH: _ALL_ROLES,
    VIEW_SPECTRUM_RESOURCE: _ALL_ROLES,
    MANAGE_INVENTORY: (Role.ADMIN,),
    # An Operator picks a Beam when creating a Satnet Path, so every role reads them.
    # Beam *engineering* is administrator-only (specification section 25): the builder
    # decides what spectrum exists, and getting it wrong is not an operator-recoverable
    # mistake.
    VIEW_BEAM: _ALL_ROLES,
    MANAGE_BEAMS: (Role.ADMIN,),
}
