"""Administration URLs, mounted at /administration/.

Kept separate from accounts/urls.py so the authentication endpoints and the
administration endpoints can be mounted at different paths with different exposure.
"""

from django.urls import path

from accounts import mfa_views, views

app_name = "administration"

urlpatterns = [
    path("users/", views.UserListView.as_view(), name="user-list"),
    path("users/<uuid:user_id>/", views.UserDetailView.as_view(), name="user-detail"),
    path("users/<uuid:user_id>/roles/", views.assign_roles, name="user-assign-roles"),
    # §21. An administrator who has lost both their authenticator and their recovery codes
    # needs somebody else to remove the factor; the event names both people.
    path("users/<uuid:user_id>/mfa/reset/", mfa_views.reset_for_user, name="user-reset-mfa"),
]
