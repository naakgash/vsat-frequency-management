"""Root URL configuration.

Navigation order follows specification section 7. Sections not yet implemented are
absent rather than stubbed, so the navigation never offers a dead link.
"""

from __future__ import annotations

from django.urls import include, path

from operations import views as operations_views

urlpatterns = [
    path("", operations_views.HomeView.as_view(), name="home"),
    path("health/", include("operations.urls")),
    path("accounts/", include("accounts.urls")),
    path("administration/", include("accounts.admin_urls")),
]

handler403 = "operations.views.permission_denied"
handler404 = "operations.views.page_not_found"
handler500 = "operations.views.server_error"
