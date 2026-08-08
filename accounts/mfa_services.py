"""Enrolling, verifying and resetting a second factor. §21, §18.

Every one of these is an audit event, and that is not ceremony. Enrolling a second factor,
using a recovery code and having somebody else reset yours are the three moments where an
account's authentication changes — which makes them the three lines an investigation reads
first.

The **secret never appears in an event**. `audit.services` redacts any field whose name
contains "secret", and nothing here passes one anyway.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from django.db import transaction
from django.utils import timezone

from accounts import mfa
from accounts.models import MfaCredential, MfaRecoveryCode, User
from audit import services as audit_services

MFA_ENROLLED = "MFA_ENROLLED"
MFA_RESET = "MFA_RESET"
MFA_VERIFIED = "MFA_VERIFIED"
MFA_FAILED = "MFA_FAILED"
MFA_RECOVERY_USED = "MFA_RECOVERY_USED"
MFA_RECOVERY_REGENERATED = "MFA_RECOVERY_REGENERATED"


class MfaError(Exception):
    """A second-factor operation was refused, with a message for the person in front of it."""


@dataclasses.dataclass(frozen=True)
class Confirmation:
    """A completed enrolment and the codes that go with it."""

    credential: MfaCredential
    recovery_codes: list[str]


def begin_enrolment(user: User) -> mfa.Enrolment:
    """Generate a secret and hold it, unconfirmed, until a code from it is entered.

    Called every time the enrolment page is opened, and it **replaces** any unconfirmed
    secret. Reusing one would mean a secret shown on a screen somebody walked away from is
    still live an hour later; regenerating costs nothing and a confirmed credential is never
    touched — :func:`reset` is the only thing that removes one.
    """
    credential, _ = MfaCredential.objects.get_or_create(user=user, defaults={"secret": ""})
    if credential.is_confirmed:
        raise MfaError(
            "This account already has a second factor. An administrator resets one; it is "
            "not replaced by enrolling again."
        )
    credential.secret = mfa.new_secret()
    credential.save(update_fields=["secret", "updated_at"])
    return mfa.enrolment_for(user, credential.secret)


def confirm_enrolment(*, user: User, code: str) -> Confirmation:
    """Prove the secret reached an authenticator, then turn the factor on. §21.

    **Deliberately not wrapped in one transaction**, unlike everything else here. The refusal
    below records an audit event and then raises — and a raise inside ``atomic`` takes the
    record with it, so the very event §18 wants most (somebody failing a second factor) would
    be the one that never survives. `accounts.policy` warns about exactly this shape. The
    writes that follow are transactional; the check that precedes them is not.
    """
    credential = MfaCredential.objects.filter(user=user).first()
    if credential is None or not credential.secret:
        raise MfaError("Start the enrolment again — there is no secret waiting to be confirmed.")
    if credential.is_confirmed:
        raise MfaError("This account already has a confirmed second factor.")

    counter = mfa.verify(credential.secret, code)
    if counter is None:
        audit_services.record(
            action=MFA_FAILED,
            actor=user,
            outcome="FAILURE",
            obj=user,
            message="A code did not match during enrolment",
        )
        raise MfaError("That code did not match. Check your device's clock and try again.")

    return _turn_on(user=user, credential=credential, counter=counter)


@transaction.atomic
def _turn_on(*, user: User, credential: MfaCredential, counter: int) -> Confirmation:
    """The writes half of :func:`confirm_enrolment`, once the code has been accepted."""
    credential.confirmed_at = timezone.now()
    credential.last_counter = counter
    credential.last_used_at = timezone.now()
    credential.save(update_fields=["confirmed_at", "last_counter", "last_used_at", "updated_at"])

    codes = _issue_recovery_codes(user)
    audit_services.record(
        action=MFA_ENROLLED,
        actor=user,
        obj=user,
        after={"recovery_codes_issued": len(codes)},
        message=f"{user.get_username()} enrolled a second factor",
    )
    return Confirmation(credential=credential, recovery_codes=codes)


@transaction.atomic
def verify_code(*, user: User, code: str) -> bool:
    """Check a code at sign-in, or a recovery code, and record either way. §18.

    A **failure is recorded too**. A run of `MFA_FAILED` events against one account is the
    signal that somebody has the password and not the phone, and that is precisely the thing
    §18's trail exists to make visible.
    """
    credential = MfaCredential.objects.select_for_update().filter(user=user).first()
    if credential is None or not credential.is_confirmed:
        return False

    counter = mfa.verify(credential.secret, code, after_counter=credential.last_counter)
    if counter is not None:
        credential.last_counter = counter
        credential.last_used_at = timezone.now()
        credential.save(update_fields=["last_counter", "last_used_at", "updated_at"])
        audit_services.record(
            action=MFA_VERIFIED, actor=user, obj=user, message="Second factor accepted"
        )
        return True

    if _spend_recovery_code(user=user, code=code):
        return True

    audit_services.record(
        action=MFA_FAILED,
        actor=user,
        outcome="FAILURE",
        obj=user,
        message="Second factor rejected",
    )
    return False


@transaction.atomic
def regenerate_recovery_codes(user: User) -> list[str]:
    """Issue a fresh set and invalidate every earlier one."""
    credential = MfaCredential.objects.filter(user=user).first()
    if credential is None or not credential.is_confirmed:
        raise MfaError("There is no confirmed second factor to issue recovery codes for.")

    codes = _issue_recovery_codes(user)
    audit_services.record(
        action=MFA_RECOVERY_REGENERATED,
        actor=user,
        obj=user,
        after={"recovery_codes_issued": len(codes)},
        message="Recovery codes reissued",
    )
    return codes


@transaction.atomic
def reset(*, actor: Any, user: User, reason: str = "") -> None:
    """Remove somebody's second factor so they can enrol again. §21.

    An administrator action, and the authorisation is the caller's — `accounts.views` requires
    ``manage_users`` before reaching here. The event names **both** people, because "who
    removed whose second factor" is the question this record exists to answer, and an event
    naming only the subject would leave the interesting half out.
    """
    MfaCredential.objects.filter(user=user).delete()
    MfaRecoveryCode.objects.filter(user=user).delete()

    audit_services.record(
        action=MFA_RESET,
        actor=actor,
        obj=user,
        change_reason=reason,
        message=(
            f"{getattr(actor, 'username', 'system')} reset the second factor for "
            f"{user.get_username()}"
        ),
    )


def status_for(user: User) -> dict[str, Any]:
    """What the administration screen shows about an account's second factor."""
    credential = MfaCredential.objects.filter(user=user).first()
    return {
        "required": mfa.is_required_for(user),
        "enrolled": bool(credential and credential.is_confirmed),
        "confirmed_at": credential.confirmed_at if credential else None,
        "last_used_at": credential.last_used_at if credential else None,
        "recovery_codes_left": MfaRecoveryCode.objects.filter(
            user=user, used_at__isnull=True
        ).count(),
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _issue_recovery_codes(user: User) -> list[str]:
    """Replace the set. Unused codes from an earlier set are deleted, used ones are kept.

    Kept, because "this account was recovered on the 3rd" is something an investigation needs
    and a row that vanishes when it is spent takes that with it.
    """
    MfaRecoveryCode.objects.filter(user=user, used_at__isnull=True).delete()
    codes = mfa.new_recovery_codes()
    MfaRecoveryCode.objects.bulk_create(
        [MfaRecoveryCode(user=user, code_hash=mfa.hash_code(code)) for code in codes]
    )
    return [mfa.format_recovery(code) for code in codes]


def _spend_recovery_code(*, user: User, code: str) -> bool:
    """Use a recovery code, once.

    Every unused code is checked rather than looked up, because they are hashed with a salt —
    which is the point. The cost is one hash comparison per outstanding code, ten times, on a
    path that runs when somebody has lost their phone.
    """
    candidate = mfa.normalise_recovery(code)
    if not candidate:
        return False

    for record in MfaRecoveryCode.objects.select_for_update().filter(
        user=user, used_at__isnull=True
    ):
        if not mfa.check_recovery(candidate, record.code_hash):
            continue
        record.used_at = timezone.now()
        record.save(update_fields=["used_at"])
        remaining = MfaRecoveryCode.objects.filter(user=user, used_at__isnull=True).count()
        audit_services.record(
            action=MFA_RECOVERY_USED,
            actor=user,
            obj=user,
            after={"recovery_codes_left": remaining},
            message=(
                f"{user.get_username()} signed in with a recovery code; {remaining} left. "
                f"A recovery code means the authenticator was unavailable."
            ),
        )
        return True
    return False
