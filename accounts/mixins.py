"""View mixins that route authorization through the policy choke point.

Django's :class:`~django.contrib.auth.mixins.PermissionRequiredMixin` refuses a request
inside ``dispatch()``, before any service function runs. A view protected only by the
stock mixin therefore denies **silently**: the user gets a 403, and nothing reaches the
audit trail. Specification section 18 requires permission denials to be recorded, so this
mixin records them on the way out.

Use :class:`AuditedPermissionRequiredMixin` everywhere the stock mixin would otherwise
appear. ``tests/permissions/test_url_coverage.py`` checks that every view declares a
permission; ``tests/permissions/test_policy.py`` checks that a view-level denial is
audited.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import HttpRequest

from accounts import policy


class AuditedPermissionRequiredMixin(PermissionRequiredMixin):
    """``PermissionRequiredMixin`` that records the denial before raising."""

    # Supplied by the View this mixin is combined with; declared so the attribute access
    # below type-checks without the mixin pretending to be a View itself.
    request: HttpRequest

    def handle_no_permission(self) -> Any:
        # Only a signed-in user's denial is a permission event worth recording. An
        # anonymous request is a redirect to the sign-in page, which is ordinary
        # behaviour rather than a refusal, and auditing every one would bury the real
        # denials in noise.
        user = getattr(self.request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            required = self.get_permission_required()
            policy.record_denial(
                user,
                " | ".join(required),
                self._denied_object(),
                detail="capability not held (view)",
            )
        return super().handle_no_permission()

    def _denied_object(self) -> Any:
        """The object being acted on, when the view has already resolved one.

        Best effort: a list view has none, and a detail view may not have fetched yet.
        """
        return getattr(self, "object", None)
