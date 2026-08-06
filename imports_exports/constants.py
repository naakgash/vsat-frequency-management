"""Capabilities and audit actions for import and export."""

from __future__ import annotations

#: §17.2. Held by every role, and narrowed by scope rather than by capability: an Observer
#: exporting "all Satnet Paths" receives their own scope, which is the same queryset the screen
#: would have shown them.
EXPORT_DATA = "imports_exports.export_data"

EXPORT_RUN = "EXPORT_RUN"
