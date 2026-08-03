"""Scoped queryset support.

docs/design/03 section 3.3: scope is applied in querysets rather than in view code, so
that "invisible" and "unwritable" cannot drift apart. Every list view, detail view, form
``ModelChoiceField``, HTMX fragment, export and dashboard card resolves through
``for_user``.
"""

from __future__ import annotations

from typing import Any, Self

from django.db import models


class ScopedQuerySet(models.QuerySet[Any]):
    """Base for querysets over scope-controlled models.

    Subclasses implement :meth:`scope_filter`, not :meth:`for_user`. That split means the
    "Admin bypasses scope" and "anonymous sees nothing" rules are stated once here and
    cannot be forgotten by a subclass.
    """

    def for_user(self, user: Any) -> Self:
        """Restrict to objects within the user's authorization scope."""
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if getattr(user, "is_admin", False):
            return self
        return self.scope_filter(user)

    def scope_filter(self, user: Any) -> Self:
        """Apply the model's scope rule for a non-admin, authenticated user."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement scope_filter(). If the model is not "
            f"scope-controlled, use a plain QuerySet instead of ScopedQuerySet."
        )
