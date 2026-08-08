"""A second factor for the accounts that can change everything. §21.

**Who has to have one.** An administrator owns the inventory, the Beam engineering, the
Specification Dictionary, users, scopes and imports (`docs/design/03` §1). An attacker with an
administrator's password can rewrite the frequency plan and grant themselves the scope to do it
again tomorrow. That is the account §21 asks to be protected by more than a password, and
:func:`is_required_for` is where "which accounts" is decided — once, so the middleware, the
sign-in flow and the administration screen cannot disagree.

**TOTP, RFC 6238.** Every authenticator application implements it, it needs no network at
verification time, and it works on a host with no route to the internet — which **OQ-17** says
this deployment may well be. A push-based or WebAuthn factor would be stronger and neither is
implementable against an intranet with no assumed connectivity.

**Recovery codes exist because the alternative is worse.** An administrator who loses their
phone with no way back needs another administrator to disable their second factor, and if they
are the only administrator the platform has locked out the person who could fix it. Ten
single-use codes, hashed with the same hasher as a password, is the standard answer and it is
the one that keeps the recovery path out of a support conversation.

**The secret is stored, and a database dump contains it.** That is a real exposure and it is
stated rather than glossed: unlike a password hash, a TOTP secret is not one-way, so anybody
holding a dump holds every second factor in it. Two things follow, both in
`docs/runbooks/backup.md`: a dump is treated as a credential store, and the field name carries
"secret" so `audit.services` redacts it and it never reaches the trail (§18).
"""

from __future__ import annotations

import dataclasses
import io
import secrets
import time
from typing import Any
from urllib.parse import quote

import pyotp
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password

#: How many 30-second steps either side of now are accepted. One, which is 90 seconds of
#: tolerance in total: enough for a phone whose clock has drifted a little and for somebody
#: typing at a normal speed, and small enough that a code shoulder-surfed a minute ago is dead.
VALID_WINDOW = 1

#: How many recovery codes an enrolment produces. Ten is enough that losing a few is not a
#: crisis and few enough that somebody will actually store them somewhere sensible.
RECOVERY_CODE_COUNT = 10

#: Characters a recovery code is drawn from. No 0/O, no 1/I/l — a code is read off paper and
#: typed, and the pairs that are indistinguishable in most fonts are the ones that generate
#: support requests.
_RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_RECOVERY_GROUPS = 2
_RECOVERY_GROUP_SIZE = 5


@dataclasses.dataclass(frozen=True)
class Enrolment:
    """A secret that has been generated and not yet confirmed."""

    secret: str
    uri: str
    #: An SVG, inline. §19.4 forbids a CDN, and sending the secret to an external QR service
    #: would post the second factor to a third party — which is the whole thing being
    #: protected. Rendered locally, embedded in the page, and it leaves no file behind.
    qr_svg: str


def issuer() -> str:
    """What the authenticator app shows above the code."""
    return str(getattr(settings, "MFA_ISSUER", "VSAT Spectrum Allocation"))


def is_required_for(user: Any) -> bool:
    """Must this account carry a second factor? §21, `docs/design/03` §2.1.

    Administrators and superusers, and everybody if a deployment says so. The list of roles is
    a setting rather than a hard-coded group name because a deployment that wants an Approver
    to carry one too should not need a code change to say so — and because turning it on for
    everybody is exactly the sort of decision that gets made after an incident.
    """
    from accounts.constants import Role

    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(settings, "MFA_REQUIRED_FOR_ALL", False):
        return True
    if getattr(user, "is_superuser", False):
        return True

    required = set(getattr(settings, "MFA_REQUIRED_ROLES", (Role.ADMIN,)))
    return bool(user.groups.filter(name__in=required).exists())


def new_secret() -> str:
    """A fresh base32 secret. ``pyotp`` draws it from :mod:`secrets`."""
    return pyotp.random_base32()


def enrolment_for(user: Any, secret: str) -> Enrolment:
    """Everything the enrolment screen needs: the secret, the URI and a QR code."""
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.get_username(), issuer_name=issuer())
    return Enrolment(secret=secret, uri=uri, qr_svg=qr_svg(uri))


def qr_svg(uri: str) -> str:
    """The provisioning URI as an inline SVG.

    SVG rather than PNG because the SVG factory in ``qrcode`` is pure Python — a PNG would
    pull in Pillow, an image library, to draw squares.
    """
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buffer = io.BytesIO()
    image.save(buffer)
    return buffer.getvalue().decode()


def verify(secret: str, code: str, *, after_counter: int | None = None) -> int | None:
    """Check a code and return the counter it matched, or ``None``.

    The **counter is returned rather than a boolean**, and that is the anti-replay mechanism: a
    code is valid for 30 seconds, so somebody who reads one over a shoulder has half a minute to
    use it. Recording the counter that was accepted and refusing anything at or below it means
    the second use of a code fails even inside its own window.
    """
    cleaned = _digits(code)
    if not cleaned:
        return None

    totp = pyotp.TOTP(secret)
    # The accepted window is walked explicitly rather than through `verify(valid_window=…)`,
    # because that returns a boolean and this needs to know *which* step matched.
    step = int(time.time()) // totp.interval
    for candidate in range(step - VALID_WINDOW, step + VALID_WINDOW + 1):
        if not secrets.compare_digest(totp.generate_otp(candidate), cleaned):
            continue
        if after_counter is not None and candidate <= after_counter:
            # Correct code, already spent. Refused, and the caller says so — telling somebody
            # "that code was already used" is more useful than "wrong code" and gives an
            # attacker nothing they did not already know.
            return None
        return candidate
    return None


def new_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    """Fresh single-use codes, in the form they are shown to a person."""
    return [_recovery_code() for _ in range(count)]


def hash_code(code: str) -> str:
    """Hash a recovery code with the project's password hasher.

    The same hasher as a password, deliberately. A recovery code *is* a credential — it signs
    somebody in on its own — and storing it in a form a database reader could use would move
    the whole second factor into the dump it was meant to survive.
    """
    return make_password(normalise_recovery(code))


def check_recovery(code: str, hashed: str) -> bool:
    return check_password(normalise_recovery(code), hashed)


def normalise_recovery(code: str) -> str:
    """A recovery code as it is stored: upper case, no separators.

    Read off paper and typed, so the hyphen the screen shows and any spaces are removed, and
    case is ignored. Nothing else is forgiven — an alphabet without ambiguous characters means
    a mistyped code is a mistyped code, not a near miss to guess at.
    """
    return "".join(character for character in code.upper() if character in _RECOVERY_ALPHABET)


def format_recovery(code: str) -> str:
    """A stored code as it is shown: grouped, with a hyphen."""
    return "-".join(
        code[index : index + _RECOVERY_GROUP_SIZE]
        for index in range(0, len(code), _RECOVERY_GROUP_SIZE)
    )


def otpauth_link(uri: str) -> str:
    return quote(uri, safe=":/?=&")


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _recovery_code() -> str:
    return "".join(
        secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_GROUPS * _RECOVERY_GROUP_SIZE)
    )


def _digits(code: str) -> str:
    return "".join(character for character in str(code) if character.isdigit())
