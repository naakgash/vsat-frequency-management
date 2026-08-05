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
VIEW_SATNET = "satnets.view_satnet"
MANAGE_SATNETS = "satnets.manage_satnets"
VIEW_SATNET_PATH = "satnet_paths.view_satnetpath"
MANAGE_SATNET_PATHS = "satnet_paths.manage_satnet_paths"
MANAGE_BEAMS = "beams.manage_beams"

#: The §15.2 lifecycle, one capability per transition (`docs/design/03` §2.2).
PLAN_SATNET_PATH = "satnet_paths.plan_satnetpath"
SUBMIT_SATNET_PATH = "satnet_paths.submit_satnetpath"
APPROVE_SATNET_PATH = "satnet_paths.approve_satnetpath"
REJECT_SATNET_PATH = "satnet_paths.reject_satnetpath"
SUSPEND_SATNET_PATH = "satnet_paths.suspend_satnetpath"
RETIRE_SATNET_PATH = "satnet_paths.retire_satnetpath"
CANCEL_SATNET_PATH = "satnet_paths.cancel_satnetpath"
REVISE_SATNET_PATH = "satnet_paths.revise_satnetpath"
VIEW_APPROVALS = "approvals.view_approvaldecision"

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
    # Every role reads Satnets; an Approver reviewing an allocation needs the context.
    VIEW_SATNET: _ALL_ROLES,
    # §25: an Operator creates Satnets — but only under a Beam and Hub they hold grants for,
    # which is object scope rather than capability and is enforced in satnets.services.
    MANAGE_SATNETS: (Role.ADMIN, Role.OPERATOR),
    # The allocation record every role needs to read: an Approver decides on one, an Observer
    # reports on it.
    VIEW_SATNET_PATH: _ALL_ROLES,
    # §9: creating allocations is the Operator's job. Object scope narrows it further — the
    # Satnet's Beam and Hub must both be granted (A-17) — and that is checked separately.
    MANAGE_SATNET_PATHS: (Role.ADMIN, Role.OPERATOR),
    # §15.2's graph, split the way `docs/design/03` §2.2 splits it. The division is the point
    # of §12's separation of duties: whoever plans a transmission does not put it on air.
    PLAN_SATNET_PATH: (Role.ADMIN, Role.OPERATOR),
    SUBMIT_SATNET_PATH: (Role.ADMIN, Role.OPERATOR),
    REVISE_SATNET_PATH: (Role.ADMIN, Role.OPERATOR),
    # Deciding, suspending and retiring are the Approver's, **including for Admin**.
    # `docs/design/03` §2.1 marks these rows "—" for administrators, and the surrounding text
    # says why: "Admin is powerful but not omnipotent". An administrator who must approve
    # something is given the Approver role, which is a grant somebody can see, rather than
    # inheriting the authority invisibly.
    APPROVE_SATNET_PATH: (Role.APPROVER,),
    REJECT_SATNET_PATH: (Role.APPROVER,),
    SUSPEND_SATNET_PATH: (Role.APPROVER,),
    RETIRE_SATNET_PATH: (Role.APPROVER,),
    # Cancelling only ever applies to a draft or planned allocation — nothing is on air — so
    # it stays with the people who work with them day to day.
    CANCEL_SATNET_PATH: (Role.ADMIN, Role.OPERATOR, Role.APPROVER),
    VIEW_APPROVALS: _ALL_ROLES,
}
