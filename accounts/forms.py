"""Account forms."""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.auth.forms import AuthenticationForm

from accounts import services
from accounts.constants import ROLE_DISPLAY_ORDER, Role


class ThrottledAuthenticationForm(AuthenticationForm):
    """Sign-in form with temporary lockout (specification sections 21.4, 21.5)."""

    error_messages = {
        **AuthenticationForm.error_messages,
        # Deliberately identical whether the username exists or not: a distinguishable
        # message turns the login form into a username oracle.
        "invalid_login": "Enter a correct username and password. Both fields are case-sensitive.",
        "inactive": "This account is not active. Contact an administrator.",
        "locked_out": (
            "Too many failed sign-in attempts. This account is temporarily locked. "
            "Try again later or contact an administrator."
        ),
    }

    def clean(self) -> dict[str, Any]:
        username = self.cleaned_data.get("username") or ""

        if username:
            state = services.lockout_state(username=username)
            if state.locked:
                # Checked before credentials are verified. Validating first would let an
                # attacker confirm a correct password even while locked out.
                services.register_failed_login(username=username, locked_out=True)
                raise forms.ValidationError(self.error_messages["locked_out"], code="locked_out")

        try:
            cleaned = super().clean()
        except forms.ValidationError:
            if username:
                state = services.lockout_state(username=username)
                # +1 accounts for the failure being recorded right now, which is not yet
                # reflected in the counts read above.
                now_locked = (
                    state.username_failures + 1 >= state.username_limit
                    or state.ip_failures + 1 >= state.ip_limit
                )
                services.register_failed_login(username=username, locked_out=now_locked)
            raise

        return cleaned


class RoleAssignmentForm(forms.Form):
    """Assign roles to a user.

    Multiple selection: roles are additive (an operator may also be an approver), so a
    single-choice control would misrepresent the model.
    """

    roles = forms.MultipleChoiceField(
        choices=[(role.value, role.label) for role in ROLE_DISPLAY_ORDER],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Roles",
        help_text="A user may hold more than one role. Clearing all roles removes access.",
    )
    reason = forms.CharField(
        max_length=500,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Why is this change being made?"}),
        label="Change reason",
        help_text="Recorded in the audit trail.",
    )

    def clean_roles(self) -> list[str]:
        roles = self.cleaned_data["roles"]
        unknown = sorted(set(roles) - set(Role.values))
        if unknown:
            raise forms.ValidationError(f"Unknown roles: {', '.join(unknown)}")
        return list(roles)
