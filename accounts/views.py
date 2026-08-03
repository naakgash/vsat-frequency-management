"""Account views: authentication and user administration.

Views stay thin: parse, authorise, call a service, render. Every authorization decision
is made by :mod:`accounts.policy`, never by the view itself, so the importer and any
future API inherit identical rules.
"""

from __future__ import annotations

from typing import Any, cast

from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView

from accounts import services
from accounts.constants import MANAGE_USERS
from accounts.forms import RoleAssignmentForm, ThrottledAuthenticationForm
from accounts.mixins import AuditedPermissionRequiredMixin
from accounts.models import User


class LoginView(auth_views.LoginView):
    """Sign in, with throttling and audit."""

    template_name = "accounts/login.html"
    authentication_form = ThrottledAuthenticationForm
    redirect_authenticated_user = True

    def form_valid(self, form: AuthenticationForm) -> HttpResponse:
        response = super().form_valid(form)
        # Runs after login(), so the session user is the authenticated account. The
        # cast states that; unlike the logout path below there is no is_authenticated
        # check here for the type checker to narrow on.
        services.register_successful_login(user=cast(User, self.request.user))
        return response


class LogoutView(auth_views.LogoutView):
    """Sign out. POST only — Django 5 no longer permits logout via GET.

    The redirect target comes from ``LOGOUT_REDIRECT_URL`` rather than a ``next_page``
    attribute, so it stays configurable per environment.
    """

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Recorded before the session is flushed, while the actor is still known.
        # is_authenticated narrows the union, so no cast is needed here.
        if request.user.is_authenticated:
            services.register_logout(user=request.user)
        return super().post(request, *args, **kwargs)


class UserListView(LoginRequiredMixin, AuditedPermissionRequiredMixin, ListView):
    """Administration: list users."""

    permission_required = MANAGE_USERS
    template_name = "administration/user_list.html"
    context_object_name = "users"
    paginate_by = 50

    def get_queryset(self) -> QuerySet[User]:
        return User.objects.prefetch_related("groups").order_by("username")


class UserDetailView(LoginRequiredMixin, AuditedPermissionRequiredMixin, DetailView):
    """Administration: view a user and their roles."""

    permission_required = MANAGE_USERS
    template_name = "administration/user_detail.html"
    context_object_name = "subject"
    slug_field = "id"
    slug_url_kwarg = "user_id"

    def get_queryset(self) -> QuerySet[User]:
        return User.objects.prefetch_related("groups")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        subject: User = context["subject"]
        context["role_form"] = RoleAssignmentForm(initial={"roles": sorted(subject.role_names)})
        return context


def assign_roles(request: HttpRequest, user_id: str) -> HttpResponse:
    """Replace a user's roles.

    A function view rather than a class-based one: the authorization is delegated to the
    service, which is where it must live so that any other caller is equally protected.
    The view's own ``policy.require`` is not repeated here precisely because
    ``set_user_roles`` performs it — duplicating the check invites the two copies to
    diverge.
    """
    if request.method != "POST":
        return redirect("administration:user-detail", user_id=user_id)

    subject = get_object_or_404(User, pk=user_id)
    form = RoleAssignmentForm(request.POST)

    if not form.is_valid():
        return render(
            request,
            "administration/user_detail.html",
            {"subject": subject, "role_form": form},
            status=400,
        )

    services.set_user_roles(
        actor=request.user,
        user=subject,
        roles=form.cleaned_data["roles"],
        reason=form.cleaned_data["reason"],
    )
    return HttpResponseRedirect(subject.get_absolute_url())
