"""The approval queue and the decision. §10.3, §15.2, §26.14."""

from __future__ import annotations

from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from accounts.mixins import AuditedPermissionRequiredMixin
from approvals import services
from approvals.constants import Decision
from satnet_paths import lifecycle, selectors
from satnet_paths.constants import (
    APPROVE_SATNET_PATH,
    REJECT_SATNET_PATH,
    VIEW_SATNET_PATH,
)
from satnet_paths.models import SatnetPath


class ApprovalQueueView(LoginRequiredMixin, AuditedPermissionRequiredMixin, ListView):
    """Everything awaiting a decision, within the reader's scope.

    Readable by every role that can read an allocation, not only by Approvers: an Operator
    needs to see that what they submitted is still waiting, and hiding the queue from them
    turns "where is my allocation" into a question for somebody else.
    """

    permission_required = VIEW_SATNET_PATH
    template_name = "approvals/queue.html"
    context_object_name = "paths"

    def get_queryset(self) -> Any:
        return services.pending_for(self.request.user)


class ApprovalDecisionView(LoginRequiredMixin, AuditedPermissionRequiredMixin, View):
    """`/satnet-paths/<uuid>/approve/` and `/reject/`. `docs/design/03` §6.

    The URL lives in the Satnet Path's space because that is where an approver is standing;
    the code lives here because a decision is a record, not only a status change.
    """

    def get_permission_required(self) -> Any:
        return (APPROVE_SATNET_PATH if self.kwargs["outcome"] == "approve" else REJECT_SATNET_PATH,)

    def get_permission_object(self) -> SatnetPath:
        return self.path

    @property
    def path(self) -> SatnetPath:
        if not hasattr(self, "_path"):
            self._path = get_object_or_404(
                selectors.visible(self.request.user), pk=self.kwargs["pk"]
            )
        return self._path

    def post(self, request: HttpRequest, pk: Any, outcome: str) -> HttpResponse:
        decision = Decision.APPROVED if outcome == "approve" else Decision.REJECTED
        try:
            services.decide(
                actor=request.user,
                path=self.path,
                decision=decision,
                comment=request.POST.get("comment", ""),
                reason=request.POST.get("change_reason", ""),
                expected_version=_submitted_version(request),
            )
        except lifecycle.StaleRecordError as exc:
            return render(
                request,
                "satnet_paths/stale.html",
                {
                    "path": self.path,
                    "changes": exc.changes,
                    "current_version": exc.current_version,
                },
                status=409,
            )
        except services.ApprovalRefused as exc:
            # 409, matching the wizard: the submission is well-formed and was refused by a rule
            # about the world or about who is asking, not by a field anybody can retype.
            return render(
                request,
                "satnet_paths/refused.html",
                {"path": self.path, "message": str(exc)},
                status=409,
            )

        messages.success(request, f"{self.path.code} is now {self.path.status}.")
        return redirect(self.path.get_absolute_url())


def _submitted_version(request: HttpRequest) -> int | None:
    raw = request.POST.get("record_version")
    return int(raw) if raw and raw.isdigit() else None
