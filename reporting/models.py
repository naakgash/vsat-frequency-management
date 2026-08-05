"""A table somebody set up and wants back. §10.3, `docs/design/02` §8.

A saved view is a *presentation* record: which filters were applied, which columns were shown,
how it was sorted. It holds no allocation data and grants no access — the queryset it produces
is scope-filtered on every read like any other listing (`docs/design/03` §4), so a view shared
by an administrator shows an Observer only what that Observer may already see.

That is the reason `is_shared` is safe to have at all: sharing a view shares the *question*,
never the answer.
"""

from __future__ import annotations

import uuid

from django.db import models


class SavedView(models.Model):
    """One person's saved table setup, optionally offered to everybody else."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="saved_views")
    name = models.CharField(max_length=100)
    #: Which table this view belongs to. One page today; the column registry is per page and a
    #: view for one would be nonsense on another, so the discriminator ships now rather than
    #: being retrofitted when the second table arrives.
    page = models.CharField(max_length=50, default="satnet_paths")

    filters = models.JSONField(default=dict, blank=True)
    columns = models.JSONField(default=list, blank=True)
    sort = models.CharField(max_length=60, blank=True)

    is_shared = models.BooleanField(
        default=False,
        help_text="Offer this view to everybody. They still see only what their scope allows.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "saved_view"
        ordering = ["name"]
        default_permissions = ("view", "add", "change", "delete")
        constraints = [
            # Per owner, per page. Two views called "Ka-band FWD" belonging to one person is a
            # naming accident, not a feature; two people using the same name is neither.
            models.UniqueConstraint(
                fields=["owner", "page", "name"], name="uq_saved_view_owner_page_name"
            ),
        ]
        indexes = [
            models.Index(fields=["page", "is_shared"], name="saved_view_shared_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.owner})"

    @property
    def query_string(self) -> str:
        """This view as the query string that would reproduce it.

        The table reads its state from the URL, so applying a saved view is a redirect rather
        than a second code path — which means a shared link and a saved view cannot disagree
        about what they show.
        """
        from urllib.parse import urlencode

        parameters: list[tuple[str, str]] = [
            (key, str(value))
            for key, value in sorted(self.filters.items())
            if value not in ("", None)
        ]
        parameters += [("column", key) for key in self.columns]
        if self.sort:
            parameters.append(("sort", self.sort))
        return urlencode(parameters)
