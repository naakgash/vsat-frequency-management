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
    path("specifications/", include("specifications.urls")),
    path("inventory/", include("inventory.urls")),
    path("beams/", include("beams.urls")),
    path("spectrum/", include("spectrum.urls")),
    path("satnets/", include("satnets.urls")),
    path("satnet-paths/", include("satnet_paths.urls")),
    # Mounted at the root, and after the Satnet Path routes, because the two decision URLs
    # live inside `/satnet-paths/<uuid>/` (`docs/design/03` §6) while the queue is its own
    # page. The Satnet Path include cannot match `approve` or `reject` — its transition route
    # lists the six moves that are not decisions — so the order is documentation, not a
    # dependency.
    path("", include("approvals.urls")),
    path("engineering/", include("calculations.urls")),
]

handler403 = "operations.views.permission_denied"
handler404 = "operations.views.page_not_found"
handler500 = "operations.views.server_error"
