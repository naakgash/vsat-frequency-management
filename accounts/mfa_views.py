"""The second-factor screens. §21.

**Sign-in becomes two steps for the accounts that need it**, and the first step does not sign
anybody in. `LoginView` stops short of `login()` when a second factor is required and stores
the pending user in the session instead; nothing is authenticated until :class:`MfaVerifyView`
accepts a code. A flow that logged somebody in and *then* asked for a code would be a flow
where the password alone is enough for anything that happens before the redirect.

The pending state carries its own **deadline**. A half-finished sign-in left in a session is a
password that has already been accepted, and it should not still be waiting an hour later.
"""

from __future__ import annotations

import datetime
from typing import cast

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from accounts import mfa, mfa_services, services
from accounts.models import User

#: Where the half-finished sign-in lives, and until when.
PENDING_USER_KEY = "mfa_pending_user"
PENDING_SINCE_KEY = "mfa_pending_since"
#: The enrolment secret between the page being shown and a code confirming it. In the session
#: rather than the database until confirmed, so a secret nobody scanned leaves nothing behind.
#: (The name trips a "hardcoded password" lint; it is a session key naming what it
#: holds, not a credential.)
PENDING_SECRET_KEY = "mfa_pending_secret"  # noqa: S105

#: How long a password stays accepted while its second factor is outstanding. Five minutes is
#: long enough to fetch a phone and short enough that a session left on a shared screen is not
#: a way in tomorrow.
PENDING_SECONDS = 300


def pending_user(request: HttpRequest) -> User | None:
    """The account waiting on a second factor, if the wait has not expired."""
    identifier = request.session.get(PENDING_USER_KEY)
    since = request.session.get(PENDING_SINCE_KEY)
    if not identifier or not since:
        return None

    started = datetime.datetime.fromisoformat(str(since))
    if timezone.now() - started > datetime.timedelta(seconds=PENDING_SECONDS):
        clear_pending(request)
        return None
    return User.objects.filter(pk=identifier, is_active=True).first()


def start_pending(request: HttpRequest, user: User) -> None:
    request.session[PENDING_USER_KEY] = str(user.pk)
    request.session[PENDING_SINCE_KEY] = timezone.now().isoformat()


def clear_pending(request: HttpRequest) -> None:
    for key in (PENDING_USER_KEY, PENDING_SINCE_KEY):
        request.session.pop(key, None)


class MfaVerifyView(View):
    """Step two of signing in: a code from the authenticator, or a recovery code. §21."""

    template_name = "accounts/mfa_verify.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        if pending_user(request) is None:
            return redirect("accounts:login")
        return render(request, self.template_name, {})

    def post(self, request: HttpRequest) -> HttpResponse:
        user = pending_user(request)
        if user is None:
            messages.error(request, "That sign-in expired. Start again.")
            return redirect("accounts:login")

        code = request.POST.get("code", "")
        if not mfa_services.verify_code(user=user, code=code):
            # The failure is already recorded by the service (§18). The message is deliberately
            # the same whether the code was wrong, expired or already spent: an attacker
            # learning *which* narrows their next attempt.
            return render(
                request,
                self.template_name,
                {"error": "That code was not accepted. Try the next one your app shows."},
                status=401,
            )

        # Only now. Everything before this point had a password and nothing else.
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        clear_pending(request)
        services.register_successful_login(user=user)
        return redirect(request.POST.get("next") or "home")


class MfaSetupView(LoginRequiredMixin, View):
    """Enrolment. Reached by choice, or by the middleware when a factor is required. §21."""

    template_name = "accounts/mfa_setup.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        user = cast(User, request.user)
        status = mfa_services.status_for(user)
        if status["enrolled"]:
            return render(request, self.template_name, {"status": status, "enrolled": True})

        enrolment = mfa_services.begin_enrolment(user)
        # Held in the session, not the database, until a code confirms it: a secret shown on a
        # screen somebody walked away from should leave nothing behind.
        request.session[PENDING_SECRET_KEY] = enrolment.secret
        return render(
            request,
            self.template_name,
            {"enrolment": enrolment, "status": status, "enrolled": False},
        )

    def post(self, request: HttpRequest) -> HttpResponse:
        user = cast(User, request.user)
        try:
            confirmation = mfa_services.confirm_enrolment(
                user=user, code=request.POST.get("code", "")
            )
        except mfa_services.MfaError as exc:
            enrolment = mfa.enrolment_for(user, request.session.get(PENDING_SECRET_KEY, ""))
            return render(
                request,
                self.template_name,
                {"enrolment": enrolment, "error": str(exc), "enrolled": False},
                status=400,
            )

        request.session.pop(PENDING_SECRET_KEY, None)
        # Shown once, and the page says so. Storing them readably to show again later would
        # undo the reason they are hashed.
        return render(
            request,
            "accounts/mfa_recovery_codes.html",
            {"codes": confirmation.recovery_codes, "just_enrolled": True},
        )


class MfaRecoveryCodesView(LoginRequiredMixin, View):
    """Issue a fresh set, invalidating the old. §21."""

    def post(self, request: HttpRequest) -> HttpResponse:
        try:
            codes = mfa_services.regenerate_recovery_codes(cast(User, request.user))
        except mfa_services.MfaError as exc:
            messages.error(request, str(exc))
            return redirect("accounts:mfa-setup")
        return render(request, "accounts/mfa_recovery_codes.html", {"codes": codes})


def reset_for_user(request: HttpRequest, user_id: str) -> HttpResponse:
    """An administrator removing somebody's second factor. §21, §18.

    Authorised through the policy choke point like every other administration action, and
    audited naming both people — "who removed whose" is the question the record answers.
    """
    from django.shortcuts import get_object_or_404

    from accounts import policy
    from accounts.constants import MANAGE_USERS

    policy.require(request.user, MANAGE_USERS, reason=request.POST.get("reason", ""))
    subject = get_object_or_404(User, pk=user_id)

    if request.method != "POST":
        return redirect("administration:user-detail", user_id=user_id)

    mfa_services.reset(actor=request.user, user=subject, reason=request.POST.get("reason", ""))
    messages.success(
        request,
        f"Removed the second factor for {subject.get_username()}. They will be asked to "
        f"enrol again the next time they sign in.",
    )
    return redirect("administration:user-detail", user_id=user_id)


def next_step_for(request: HttpRequest, user: User) -> str | None:
    """Where a freshly authenticated user has to go before they are signed in, if anywhere.

    One function, called by `LoginView`, so the sign-in flow and the middleware cannot come to
    different conclusions about who needs a second factor.
    """
    status = mfa_services.status_for(user)
    if status["enrolled"]:
        start_pending(request, user)
        return reverse("accounts:mfa-verify")
    if status["required"]:
        # Required but not enrolled. They are signed in and the middleware sends them to
        # enrolment on every page until they finish — refusing the sign-in instead would lock
        # out every administrator the day this is switched on, including the one who could
        # switch it off.
        return None
    return None
