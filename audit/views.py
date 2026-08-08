"""Reading the trail on a screen. §18, §26.17.

**Read-only, and there is no other kind of route here.** No form, no POST handler, no delete
view, no admin registration. §18 and **A-15** make an audit event immutable, and the database
enforces that with a trigger — but the absence of a write route is the first line, and
``tests/audit`` asserts that every URL this module publishes is a GET.

**Authorisation without reaching upward.** Every other module protects a view with
``accounts.mixins.AuditedPermissionRequiredMixin``, which records the denial through
``accounts.policy``. `audit` may import no local module (`docs/design/01` §1) — it is the
bottom of the graph precisely because everything above records into it — so the mixin here does
the same job with the recorder that already lives in this module. The behaviour is deliberately
identical: 403, and a `PERMISSION_DENIED` event with the capability that was missing.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import Http404, HttpRequest
from django.views.generic import DetailView, TemplateView

from audit import constants, selectors, services
from audit.models import AuditEvent

#: Rows per page. §18's trail is the largest table in the product and **OQ-15** does not say how
#: large; a page is a bound on what one request renders, not a guess at the total.
PAGE_SIZE = 50


class AuditPermissionRequiredMixin(PermissionRequiredMixin):
    """``PermissionRequiredMixin`` that records the denial before raising.

    The twin of ``accounts.mixins.AuditedPermissionRequiredMixin``, written here rather than
    imported because of the layering rule in this module's docstring. It records through
    ``audit.services.record`` directly, which is what ``accounts.policy`` does anyway.
    """

    request: HttpRequest

    def handle_no_permission(self) -> Any:
        # An anonymous request is a redirect to the sign-in page, which is ordinary behaviour
        # rather than a refusal. Auditing every one would bury the real denials in noise —
        # the same judgement `accounts.policy` makes.
        user = getattr(self.request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            services.record(
                action=constants.PERMISSION_DENIED,
                actor=user,
                outcome=constants.AuditOutcome.FAILURE,
                message=(
                    f"Denied '{' | '.join(self.get_permission_required())}': "
                    f"capability not held (view)"
                ),
            )
        return super().handle_no_permission()


class AuditSearchView(LoginRequiredMixin, AuditPermissionRequiredMixin, TemplateView):
    """§18's search: by actor, by action, by object, by period, by request, by import batch.

    **The URL is the state**, as it is on the Satnet Path table: a link somebody pastes into an
    incident channel has to show the recipient the same rows — narrowed to what *they* may see,
    which is the one thing a shared link must not carry with it.
    """

    permission_required = selectors.VIEW_AUDIT
    template_name = "audit/search.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        request = self.request
        filters = selectors.clean(request.GET.dict())
        page = Paginator(selectors.search(request.user, filters), PAGE_SIZE).get_page(
            request.GET.get("page")
        )
        return {
            **super().get_context_data(**kwargs),
            "page": page,
            "events": page.object_list,
            "fields": selectors.form_fields(filters),
            "applied": selectors.describe(filters),
            "actions": selectors.actions_seen(request.user),
            "query": _query_without_page(request),
            # So the screen can say whose trail it is showing. A page that silently held back
            # two thirds of the events is how somebody concludes the platform lost them.
            "sees_everything": request.user.has_perm(selectors.VIEW_ALL_AUDIT),
        }


class AuditEventView(LoginRequiredMixin, AuditPermissionRequiredMixin, DetailView):
    """One event, with the field-level difference §18 asks for."""

    permission_required = selectors.VIEW_AUDIT
    template_name = "audit/event.html"
    context_object_name = "event"

    def get_queryset(self) -> QuerySet[AuditEvent]:
        # Visibility is the queryset, not a check after the fetch: an event somebody may not
        # read has to be a 404 rather than a 403, because "this event exists" is itself
        # something the trail should not disclose.
        return selectors.visible(self.request.user)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        event: AuditEvent = self.object
        return {
            **super().get_context_data(**kwargs),
            "changes": services.diff(event.before or {}, event.after or {}),
            "has_payload": bool(event.before or event.after),
        }


class ObjectHistoryView(LoginRequiredMixin, AuditPermissionRequiredMixin, TemplateView):
    """Everything recorded about one record, oldest first. §26.17.

    Reached by `app_label.ModelName` and identifier rather than by a route per entity: the trail
    holds rows about twenty kinds of object and a view each would be twenty places to forget the
    visibility rule.
    """

    permission_required = selectors.VIEW_AUDIT
    template_name = "audit/history.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        object_type = str(self.kwargs["object_type"])
        try:
            object_id = uuid.UUID(str(self.kwargs["object_id"]))
        except ValueError as exc:  # pragma: no cover - the URL converter already refuses this
            raise Http404("That is not an identifier this platform issued.") from exc

        events = list(selectors.history_of(self.request.user, object_type, object_id))
        return {
            **super().get_context_data(**kwargs),
            "object_type": object_type,
            "object_id": object_id,
            "events": events,
            # The most recent representation the trail holds. Read from the events rather than
            # by fetching the object: audit rows outlive what they describe, and a history that
            # 404s once a record is gone is a history of nothing.
            "object_repr": next(
                (event.object_repr for event in reversed(events) if event.object_repr), ""
            ),
            "entries": [
                {"event": event, "changes": services.diff(event.before or {}, event.after or {})}
                for event in events
            ],
        }


def _query_without_page(request: HttpRequest) -> str:
    """The current query string with ``page`` removed, for the pager to append its own."""
    parameters = request.GET.copy()
    parameters.pop("page", None)
    return parameters.urlencode()
