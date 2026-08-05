"""Capabilities and audit actions for the table, saved views and the dashboard."""

from __future__ import annotations

#: Saving a view is not an operational action and every role does it for themselves. The
#: capability exists so the URL can declare one — `tests/permissions/test_url_coverage.py`
#: requires it — rather than because there is a role that should be without it.
MANAGE_SAVED_VIEWS = "reporting.add_savedview"

VIEW_SAVED = "SAVED_VIEW_SAVED"
VIEW_DELETED = "SAVED_VIEW_DELETED"
