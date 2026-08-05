"""Writing saved views. §10.3, §18.

Small on purpose. A saved view holds no allocation data, so there is no transaction to get
right and no constraint to translate — what there *is* to get right is that one person cannot
edit or delete another's, and that a shared view stays owned by whoever made it.
"""

from __future__ import annotations

from typing import Any

from accounts.models import User
from accounts.types import Actor
from audit import services as audit_services
from reporting import columns as column_registry
from reporting import filters as filter_registry
from reporting.constants import VIEW_DELETED, VIEW_SAVED
from reporting.models import SavedView


class NotYours(Exception):
    """Somebody else's saved view. Not a permission *capability* — an ownership fact.

    Separate from ``PermissionDenied`` because the answer differs: holding the capability is
    what lets anybody save views at all, and this is about which row they are pointing at.
    """


def save(
    *,
    actor: Actor,
    name: str,
    filters: dict[str, str],
    columns: list[str],
    sort: str = "",
    is_shared: bool = False,
    page: str = "satnet_paths",
) -> SavedView:
    """Create or replace one of the actor's own views.

    Replacing rather than erroring on a repeated name: `uq_saved_view_owner_page_name` makes
    the second save a constraint violation otherwise, and "save" over an existing name is what
    somebody adjusting a view means by it.

    The filters and columns are **re-cleaned** on the way in. They arrive from a query string,
    and a view that stored an unknown key would hand it to the table every time it was applied.
    """
    if not isinstance(actor, User):
        raise NotYours("A saved view belongs to a signed-in person.")

    stored_filters = filter_registry.clean(filters)
    stored_columns = [column.key for column in column_registry.resolve(columns)]

    view, created = SavedView.objects.update_or_create(
        owner=actor,
        page=page,
        name=name.strip(),
        defaults={
            "filters": stored_filters,
            "columns": stored_columns,
            "sort": sort,
            "is_shared": is_shared,
        },
    )
    audit_services.record(
        action=VIEW_SAVED,
        actor=actor,
        obj=view,
        after={
            "name": view.name,
            "filters": stored_filters,
            "columns": stored_columns,
            "is_shared": is_shared,
            "created": created,
        },
        message=f"Saved view {view.name}",
    )
    return view


def delete(*, actor: Actor, view: SavedView) -> None:
    """Remove one of the actor's own views.

    An administrator is not exempt. A saved view is a personal working tool rather than
    operational data, and §20's "no hard deletes" is about the record of what was allocated —
    which this is not.
    """
    if not isinstance(actor, User) or view.owner_id != actor.pk:
        raise NotYours("A saved view can only be deleted by the person who made it.")

    before: dict[str, Any] = {"name": view.name, "filters": view.filters, "columns": view.columns}
    view.delete()
    audit_services.record(
        action=VIEW_DELETED,
        actor=actor,
        before=before,
        message=f"Deleted view {before['name']}",
    )
