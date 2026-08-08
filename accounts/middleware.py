"""Making the second factor unavoidable for the accounts that need one. §21.

A second factor that somebody can decline is not a control, it is a preference. §21 asks for
MFA on administrator accounts, so an administrator without one is sent to enrolment on every
page until they finish.

**Redirect rather than refuse.** The alternative — refusing the sign-in outright — locks out
every administrator the moment this is switched on, including the one who would switch it off.
An account in this state is signed in and can reach exactly two things: the enrolment page and
the sign-out button.

**Nothing is exempt by pattern.** The allow list is short, explicit and made of resolved URL
names rather than path prefixes: a prefix check is how ``/accounts/mfa/setup/../../admin``
becomes an exemption.
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

#: The only things reachable while a required second factor is outstanding. Sign-out is here
#: because somebody who cannot enrol right now must still be able to leave, and the health
#: endpoints because an orchestrator polling them has no session at all.
ALLOWED_URL_NAMES = frozenset(
    {
        "accounts:mfa-setup",
        "accounts:mfa-recovery-codes",
        "accounts:logout",
        "accounts:login",
        "health-live",
        "health-ready",
    }
)


class RequireMfaMiddleware:
    """Send an administrator without a second factor to enrolment, on every page. §21."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self.get_response(request)

    def process_view(
        self, request: HttpRequest, view_func: object, view_args: object, view_kwargs: object
    ) -> HttpResponse | None:
        """Decide after the URL has been resolved, not before.

        ``resolver_match`` is populated by the resolution step, which runs *after* every
        middleware's ``__call__`` has been entered — so a check in ``__call__`` would see no
        route name and exempt everything, or nothing. ``process_view`` is the hook that runs
        with the resolved route in hand, which is what the allow list is written against.
        """
        if self._must_enrol(request):
            return redirect("accounts:mfa-setup")
        return None

    def _must_enrol(self, request: HttpRequest) -> bool:
        from accounts import mfa
        from accounts.models import MfaCredential

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        if self._route_of(request) in ALLOWED_URL_NAMES:
            return False
        if not mfa.is_required_for(user):
            return False
        return not MfaCredential.objects.filter(user=user, confirmed_at__isnull=False).exists()

    @staticmethod
    def _route_of(request: HttpRequest) -> str:
        """The resolved route name, namespaced.

        Resolved rather than matched against the path, because a path check is how a crafted
        URL becomes an exemption. A request that resolved to nothing is not exempt — it is on
        its way to a 404, and a 404 is a fine thing for an un-enrolled administrator to get.
        """
        match = getattr(request, "resolver_match", None)
        return getattr(match, "view_name", "") if match else ""
