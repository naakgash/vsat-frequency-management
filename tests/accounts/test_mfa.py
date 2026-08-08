"""A second factor on the accounts that can change everything. §21, §18.

Four properties, and the rest is detail.

**A password alone does not sign in an administrator.** The sign-in view stops short of
``login()`` when a second factor is confirmed, so nothing is authenticated until a code is
accepted. The test that matters is not that the code works — it is that the *session is not
authenticated* between the two steps.

**A required factor cannot be declined.** An administrator without one is sent to enrolment on
every page. A second factor somebody can walk past is a preference, not a control.

**A code cannot be used twice.** A TOTP code is valid for thirty seconds, so one read over a
shoulder is live for half a minute unless the accepted step is recorded and refused after.

**Everything is audited** — including failures, because a run of them against one account is
the signal that somebody has the password and not the phone.

These tests run with ``MFA_REQUIRED_ROLES`` set back to the **production** value. The test
settings turn it off so the rest of the suite can sign in as an administrator without enrolling
first; one test below asserts the production setting is still what it should be, so switching
it off there can never quietly become switching it off everywhere.
"""

from __future__ import annotations

import pyotp
import pytest
from django.test import override_settings
from django.urls import reverse

from accounts import mfa, mfa_services
from accounts.constants import Role
from accounts.models import MfaCredential, MfaRecoveryCode
from audit.models import AuditEvent
from tests.factories import TEST_PASSWORD, make_admin, make_operator

pytestmark = pytest.mark.django_db

#: The production value, restored for this file. See the module note.
REQUIRE_MFA = override_settings(MFA_REQUIRED_ROLES=(Role.ADMIN,))


@pytest.fixture
def admin(seeded_roles):
    return make_admin("mfa-admin")


@pytest.fixture
def enrolled(admin):
    """An administrator with a confirmed second factor, enrolled the way a person would."""
    enrolment = mfa_services.begin_enrolment(admin)
    mfa_services.confirm_enrolment(user=admin, code=pyotp.TOTP(enrolment.secret).now())
    admin.refresh_from_db()
    return admin


def current_code(user) -> str:
    return pyotp.TOTP(MfaCredential.objects.get(user=user).secret).now()


def next_code(user) -> str:
    """A code the credential has not already spent.

    The `enrolled` fixture confirms with the code current at that instant, which records the
    step and — correctly — refuses it ever after. A real person waits for their app to roll
    over; a test that reused the confirming code would be asserting that replay protection is
    broken. `test_the_confirming_code_cannot_then_sign_in` pins that behaviour deliberately.
    """
    import time

    secret = MfaCredential.objects.get(user=user).secret
    return pyotp.TOTP(secret).generate_otp(int(time.time()) // 30 + 1)


# ---------------------------------------------------------------------------
# Who needs one
# ---------------------------------------------------------------------------
def test_the_production_setting_still_requires_a_second_factor_for_administrators():
    """The test settings turn this off so the rest of the suite can sign in. This asserts
    that doing so has not quietly turned it off everywhere."""
    import config.settings.base as base

    assert Role.ADMIN in base.MFA_REQUIRED_ROLES


@REQUIRE_MFA
def test_an_administrator_needs_one(admin):
    assert mfa.is_required_for(admin)


@REQUIRE_MFA
def test_an_operator_does_not(seeded_roles):
    assert not mfa.is_required_for(make_operator("mfa-operator"))


@REQUIRE_MFA
def test_a_superuser_needs_one_whatever_their_roles(seeded_roles):
    from tests.factories import make_user

    assert mfa.is_required_for(make_user("mfa-super", is_superuser=True))


@override_settings(MFA_REQUIRED_FOR_ALL=True)
def test_a_deployment_can_require_one_of_everybody(seeded_roles):
    """The decision that gets made after an incident, and it should not need a code change."""
    assert mfa.is_required_for(make_operator("mfa-everyone"))


def test_an_anonymous_visitor_needs_nothing():
    from django.contrib.auth.models import AnonymousUser

    assert not mfa.is_required_for(AnonymousUser())


# ---------------------------------------------------------------------------
# Enrolment
# ---------------------------------------------------------------------------
def test_enrolment_produces_a_secret_a_uri_and_a_qr_code(admin):
    enrolment = mfa_services.begin_enrolment(admin)

    assert len(enrolment.secret) >= 16
    assert enrolment.uri.startswith("otpauth://totp/")
    assert "<svg" in enrolment.qr_svg


def test_the_qr_code_is_rendered_here_and_not_fetched(admin):
    """§19.4 forbids a CDN, and a hosted QR service would mean posting the second factor to
    a third party — which is precisely the thing being protected."""
    enrolment = mfa_services.begin_enrolment(admin)

    assert "http://" not in enrolment.qr_svg.replace("http://www.w3.org", "")


def test_a_factor_is_not_on_until_a_code_confirms_it(admin):
    """Somebody who opened the enrolment page and closed it must not be locked out."""
    mfa_services.begin_enrolment(admin)

    assert not mfa_services.status_for(admin)["enrolled"]


def test_a_wrong_code_does_not_confirm_and_is_recorded(admin):
    mfa_services.begin_enrolment(admin)

    with pytest.raises(mfa_services.MfaError):
        mfa_services.confirm_enrolment(user=admin, code="000000")

    assert not mfa_services.status_for(admin)["enrolled"]
    assert AuditEvent.objects.filter(action=mfa_services.MFA_FAILED).exists()


def test_confirming_turns_it_on_and_issues_recovery_codes(admin):
    enrolment = mfa_services.begin_enrolment(admin)

    confirmation = mfa_services.confirm_enrolment(
        user=admin, code=pyotp.TOTP(enrolment.secret).now()
    )

    assert len(confirmation.recovery_codes) == mfa.RECOVERY_CODE_COUNT
    assert mfa_services.status_for(admin)["enrolled"]
    assert AuditEvent.objects.filter(action=mfa_services.MFA_ENROLLED).exists()


def test_opening_the_enrolment_page_again_replaces_an_unconfirmed_secret(admin):
    """A secret shown on a screen somebody walked away from should not still be live."""
    first = mfa_services.begin_enrolment(admin).secret
    second = mfa_services.begin_enrolment(admin).secret

    assert first != second


def test_a_confirmed_factor_is_never_replaced_by_enrolling_again(enrolled):
    with pytest.raises(mfa_services.MfaError) as caught:
        mfa_services.begin_enrolment(enrolled)

    assert "administrator resets one" in str(caught.value)


def test_the_secret_never_reaches_the_audit_trail(enrolled):
    """The field name carries "secret", so `audit.services` redacts it (§18)."""
    secret = MfaCredential.objects.get(user=enrolled).secret

    for event in AuditEvent.objects.all():
        assert secret not in str(event.before) + str(event.after) + event.message


# ---------------------------------------------------------------------------
# Verification and replay
# ---------------------------------------------------------------------------
def test_the_right_code_is_accepted(enrolled):
    assert mfa_services.verify_code(user=enrolled, code=next_code(enrolled))
    assert AuditEvent.objects.filter(action=mfa_services.MFA_VERIFIED).exists()


def test_the_confirming_code_cannot_then_sign_in(enrolled):
    """The code that turned the factor on is spent, like any other."""
    assert not mfa_services.verify_code(user=enrolled, code=current_code(enrolled))


def test_a_wrong_code_is_refused_and_recorded(enrolled):
    assert not mfa_services.verify_code(user=enrolled, code="000000")
    assert AuditEvent.objects.filter(action=mfa_services.MFA_FAILED).exists()


def test_the_same_code_cannot_be_used_twice(enrolled):
    """A code is live for thirty seconds. Recording the accepted step is what closes that."""
    code = next_code(enrolled)

    assert mfa_services.verify_code(user=enrolled, code=code)
    assert not mfa_services.verify_code(user=enrolled, code=code)


def test_a_code_from_the_adjacent_window_is_accepted(enrolled):
    """A phone whose clock has drifted a little still works — one step either side."""
    import time

    secret = MfaCredential.objects.get(user=enrolled).secret
    step = int(time.time()) // 30

    assert mfa.verify(secret, pyotp.TOTP(secret).generate_otp(step - 1)) is not None


def test_a_code_from_far_outside_the_window_is_refused(enrolled):
    import time

    secret = MfaCredential.objects.get(user=enrolled).secret
    step = int(time.time()) // 30

    assert mfa.verify(secret, pyotp.TOTP(secret).generate_otp(step - 20)) is None


def test_an_account_with_no_factor_verifies_nothing(admin):
    assert not mfa_services.verify_code(user=admin, code="000000")


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------
def test_a_recovery_code_signs_in_once(admin):
    enrolment = mfa_services.begin_enrolment(admin)
    confirmation = mfa_services.confirm_enrolment(
        user=admin, code=pyotp.TOTP(enrolment.secret).now()
    )
    code = confirmation.recovery_codes[0]

    assert mfa_services.verify_code(user=admin, code=code)
    assert not mfa_services.verify_code(user=admin, code=code)


def test_a_recovery_code_is_read_off_paper_so_case_and_hyphens_are_forgiven(admin):
    enrolment = mfa_services.begin_enrolment(admin)
    confirmation = mfa_services.confirm_enrolment(
        user=admin, code=pyotp.TOTP(enrolment.secret).now()
    )
    code = confirmation.recovery_codes[0]

    assert mfa_services.verify_code(user=admin, code=code.lower().replace("-", " "))


def test_recovery_codes_are_stored_hashed(enrolled):
    """A recovery code *is* a credential. Storing it readably would put the whole second
    factor back into the database dump it exists to survive."""
    for record in MfaRecoveryCode.objects.filter(user=enrolled):
        assert record.code_hash.count("$") >= 2  # a Django hasher's format


def test_a_used_recovery_code_is_kept_rather_than_deleted(admin):
    """ "This account was recovered on the 3rd" is something an investigation needs."""
    enrolment = mfa_services.begin_enrolment(admin)
    confirmation = mfa_services.confirm_enrolment(
        user=admin, code=pyotp.TOTP(enrolment.secret).now()
    )
    mfa_services.verify_code(user=admin, code=confirmation.recovery_codes[0])

    assert MfaRecoveryCode.objects.filter(user=admin, used_at__isnull=False).count() == 1


def test_using_a_recovery_code_is_recorded_with_how_many_are_left(admin):
    enrolment = mfa_services.begin_enrolment(admin)
    confirmation = mfa_services.confirm_enrolment(
        user=admin, code=pyotp.TOTP(enrolment.secret).now()
    )
    mfa_services.verify_code(user=admin, code=confirmation.recovery_codes[0])

    event = AuditEvent.objects.get(action=mfa_services.MFA_RECOVERY_USED)
    assert event.after["recovery_codes_left"] == mfa.RECOVERY_CODE_COUNT - 1


def test_reissuing_invalidates_every_earlier_unused_code(admin):
    enrolment = mfa_services.begin_enrolment(admin)
    confirmation = mfa_services.confirm_enrolment(
        user=admin, code=pyotp.TOTP(enrolment.secret).now()
    )
    old = confirmation.recovery_codes[0]

    mfa_services.regenerate_recovery_codes(admin)

    assert not mfa_services.verify_code(user=admin, code=old)


def test_recovery_codes_avoid_the_characters_nobody_can_read(admin):
    for code in mfa.new_recovery_codes(50):
        assert not set(code) & set("O01Il")


# ---------------------------------------------------------------------------
# Reset — the way back
# ---------------------------------------------------------------------------
def test_an_administrator_can_remove_somebody_elses_factor(enrolled, seeded_roles):
    other = make_admin("mfa-other-admin")

    mfa_services.reset(actor=other, user=enrolled, reason="lost phone")

    assert not mfa_services.status_for(enrolled)["enrolled"]
    assert not MfaRecoveryCode.objects.filter(user=enrolled).exists()


def test_a_reset_names_both_people(enrolled, seeded_roles):
    """ "Who removed whose second factor" is the question this record exists to answer."""
    other = make_admin("mfa-resetter")

    mfa_services.reset(actor=other, user=enrolled, reason="lost phone")

    event = AuditEvent.objects.get(action=mfa_services.MFA_RESET)
    assert "mfa-resetter" in event.message
    assert enrolled.get_username() in event.message
    assert event.change_reason == "lost phone"


@REQUIRE_MFA
def test_an_operator_cannot_reset_a_second_factor(enrolled, client, seeded_roles):
    """`policy.require` raises, and Django renders it as a 403 — the denial is recorded (§18)."""
    client.force_login(make_operator("mfa-nosy"))

    response = client.post(
        reverse("administration:user-reset-mfa", kwargs={"user_id": enrolled.pk})
    )

    assert response.status_code == 403
    assert mfa_services.status_for(enrolled)["enrolled"]
    assert AuditEvent.objects.filter(action="PERMISSION_DENIED").exists()


# ---------------------------------------------------------------------------
# Signing in — the property that matters most
# ---------------------------------------------------------------------------
@REQUIRE_MFA
def test_a_password_alone_does_not_sign_in_an_enrolled_administrator(enrolled, client):
    """The heart of §21. Between the two steps the session is **not** authenticated."""
    response = client.post(
        reverse("accounts:login"),
        {"username": enrolled.username, "password": TEST_PASSWORD},
    )

    assert response.status_code == 302
    assert response.url == reverse("accounts:mfa-verify")
    assert "_auth_user_id" not in client.session


@REQUIRE_MFA
def test_the_second_step_completes_the_sign_in(enrolled, client):
    client.post(
        reverse("accounts:login"),
        {"username": enrolled.username, "password": TEST_PASSWORD},
    )

    client.post(reverse("accounts:mfa-verify"), {"code": next_code(enrolled)})

    assert client.session["_auth_user_id"] == str(enrolled.pk)


@REQUIRE_MFA
def test_a_wrong_code_at_the_second_step_leaves_the_session_unauthenticated(enrolled, client):
    client.post(
        reverse("accounts:login"),
        {"username": enrolled.username, "password": TEST_PASSWORD},
    )

    response = client.post(reverse("accounts:mfa-verify"), {"code": "000000"})

    assert response.status_code == 401
    assert "_auth_user_id" not in client.session


@REQUIRE_MFA
def test_the_verify_page_refuses_a_visitor_with_no_pending_sign_in(client):
    """Not open: it is unauthenticated by necessity, and it does nothing without a pending
    sign-in in the session."""
    response = client.get(reverse("accounts:mfa-verify"))

    assert response.status_code == 302
    assert response.url == reverse("accounts:login")


@REQUIRE_MFA
def test_a_pending_sign_in_expires(enrolled, client):
    """A password that has already been accepted should not still be waiting an hour later."""
    from accounts import mfa_views

    client.post(
        reverse("accounts:login"),
        {"username": enrolled.username, "password": TEST_PASSWORD},
    )
    session = client.session
    session[mfa_views.PENDING_SINCE_KEY] = "2020-01-01T00:00:00+00:00"
    session.save()

    response = client.post(reverse("accounts:mfa-verify"), {"code": next_code(enrolled)})

    assert response.status_code == 302
    assert "_auth_user_id" not in client.session


@REQUIRE_MFA
def test_a_user_who_needs_no_factor_signs_in_in_one_step(seeded_roles, client):
    operator = make_operator("mfa-one-step")

    client.post(
        reverse("accounts:login"),
        {"username": operator.username, "password": TEST_PASSWORD},
    )

    assert client.session["_auth_user_id"] == str(operator.pk)


# ---------------------------------------------------------------------------
# The middleware — a factor somebody can decline is not a control
# ---------------------------------------------------------------------------
@REQUIRE_MFA
def test_an_un_enrolled_administrator_is_sent_to_enrolment_on_every_page(admin, client):
    client.force_login(admin)

    response = client.get(reverse("reporting:satnet-paths"))

    assert response.status_code == 302
    assert response.url == reverse("accounts:mfa-setup")


@REQUIRE_MFA
def test_they_can_still_reach_enrolment_and_the_way_out(admin, client):
    """Signed in, and able to reach exactly two things: enrolment and sign-out."""
    client.force_login(admin)

    assert client.get(reverse("accounts:mfa-setup")).status_code == 200
    assert client.post(reverse("accounts:logout")).status_code == 302


@REQUIRE_MFA
def test_an_enrolled_administrator_is_not_redirected(enrolled, client):
    client.force_login(enrolled)

    assert client.get(reverse("reporting:satnet-paths")).status_code == 200


@REQUIRE_MFA
def test_an_operator_is_never_redirected(seeded_roles, client):
    client.force_login(make_operator("mfa-untouched"))

    assert client.get(reverse("reporting:satnet-paths")).status_code == 200


@REQUIRE_MFA
def test_the_health_endpoints_stay_reachable(admin, client):
    """An orchestrator polling them has no session at all, and must never be redirected."""
    client.force_login(admin)

    assert client.get("/health/live").status_code == 200


@REQUIRE_MFA
def test_the_allow_list_is_route_names_not_path_prefixes(admin, client):
    """A prefix check is how `/accounts/mfa/setup/../../admin` becomes an exemption."""
    from accounts.middleware import ALLOWED_URL_NAMES

    client.force_login(admin)

    assert all(":" in name or "-" in name for name in ALLOWED_URL_NAMES)
    assert client.get("/accounts/mfa/setup/../../").status_code in (301, 302, 404)


# ---------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------
@REQUIRE_MFA
def test_the_enrolment_page_shows_a_qr_code_and_the_key(admin, client):
    client.force_login(admin)

    response = client.get(reverse("accounts:mfa-setup"))

    assert b"<svg" in response.content
    assert b"Enter this key by hand" in response.content


@REQUIRE_MFA
def test_enrolling_through_the_screen_shows_the_recovery_codes_once(admin, client):
    client.force_login(admin)
    client.get(reverse("accounts:mfa-setup"))
    secret = MfaCredential.objects.get(user=admin).secret

    response = client.post(reverse("accounts:mfa-setup"), {"code": pyotp.TOTP(secret).now()})

    assert response.status_code == 200
    assert b"This page is shown once" in response.content
    assert mfa_services.status_for(admin)["enrolled"]


@REQUIRE_MFA
def test_the_administration_screen_shows_whether_a_factor_is_enrolled(enrolled, client):
    client.force_login(enrolled)

    response = client.get(reverse("administration:user-detail", kwargs={"user_id": enrolled.pk}))

    assert b"Second factor" in response.content
    assert b"recovery code" in response.content
