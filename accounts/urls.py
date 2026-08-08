"""Account URLs: signing in, signing out, and the second factor §21 requires."""

from django.urls import path

from accounts import mfa_views, views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    # Step two of signing in. Deliberately *not* behind LoginRequiredMixin: the whole point
    # is that nobody is signed in yet — the pending account lives in the session.
    path("mfa/verify/", mfa_views.MfaVerifyView.as_view(), name="mfa-verify"),
    path("mfa/setup/", mfa_views.MfaSetupView.as_view(), name="mfa-setup"),
    path(
        "mfa/recovery-codes/",
        mfa_views.MfaRecoveryCodesView.as_view(),
        name="mfa-recovery-codes",
    ),
]
