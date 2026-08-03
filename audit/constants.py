"""Audit action vocabulary.

Actions are plain strings rather than a database enum, because every module contributes
its own and the set grows with each slice. The constants defined here are the
cross-cutting ones: authentication and authorization. Domain modules define their own
alongside the services that emit them.

Naming convention: ``<OBJECT>_<PAST_TENSE_VERB>``, upper snake case. Keep them stable —
audit history is queried by action, and renaming one orphans the records that used it.
"""

from __future__ import annotations

from django.db import models

# --- Authentication (specification section 18) -----------------------------
USER_LOGGED_IN = "USER_LOGGED_IN"
USER_LOGGED_OUT = "USER_LOGGED_OUT"
USER_LOGIN_FAILED = "USER_LOGIN_FAILED"
USER_LOCKED_OUT = "USER_LOCKED_OUT"

# --- Authorization ----------------------------------------------------------
PERMISSION_DENIED = "PERMISSION_DENIED"

# --- User, role and scope administration ------------------------------------
USER_CREATED = "USER_CREATED"
USER_UPDATED = "USER_UPDATED"
USER_ROLES_CHANGED = "USER_ROLES_CHANGED"
USER_SCOPE_GRANTED = "USER_SCOPE_GRANTED"
USER_SCOPE_REVOKED = "USER_SCOPE_REVOKED"


class AuditOutcome(models.TextChoices):
    """Whether the audited attempt succeeded.

    Failures are recorded, not discarded: a rejected login and a denied permission are
    exactly the events a security review needs (specification section 18).
    """

    SUCCESS = "SUCCESS", "Success"
    FAILURE = "FAILURE", "Failure"
