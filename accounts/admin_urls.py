"""Administration URLs, mounted at /administration/.

Kept separate from accounts/urls.py so the authentication endpoints and the
administration endpoints can be mounted at different paths with different exposure.
"""

from django.urls import path

from accounts import views

app_name = "administration"

urlpatterns = [
    path("users/", views.UserListView.as_view(), name="user-list"),
    path("users/<uuid:user_id>/", views.UserDetailView.as_view(), name="user-detail"),
    path("users/<uuid:user_id>/roles/", views.assign_roles, name="user-assign-roles"),
]
