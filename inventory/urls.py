"""Inventory URLs."""

from django.urls import path

from inventory import views

app_name = "inventory"

urlpatterns = [
    path("", views.InventoryIndexView.as_view(), name="index"),
    path("satellites/", views.SatelliteListView.as_view(), name="satellite-list"),
    path("satellites/<uuid:pk>/", views.SatelliteDetailView.as_view(), name="satellite-detail"),
    path("bands/", views.BandListView.as_view(), name="band-list"),
    path("bands/<uuid:pk>/", views.BandDetailView.as_view(), name="band-detail"),
    path("gateways/", views.GatewayListView.as_view(), name="gateway-list"),
    path("gateways/<uuid:pk>/", views.GatewayDetailView.as_view(), name="gateway-detail"),
    path("hubs/", views.HubListView.as_view(), name="hub-list"),
    path("hubs/<uuid:pk>/", views.HubDetailView.as_view(), name="hub-detail"),
    path("equipment-profiles/", views.EquipmentListView.as_view(), name="equipment-list"),
    path(
        "equipment-profiles/<uuid:pk>/",
        views.EquipmentDetailView.as_view(),
        name="equipment-detail",
    ),
    path("guard-policies/", views.GuardPolicyListView.as_view(), name="guard-policy-list"),
    path(
        "guard-policies/<uuid:pk>/",
        views.GuardPolicyDetailView.as_view(),
        name="guard-policy-detail",
    ),
    path(
        "frequency-windows/",
        views.FrequencyWindowListView.as_view(),
        name="frequency-window-list",
    ),
    path(
        "frequency-windows/<uuid:pk>/",
        views.FrequencyWindowDetailView.as_view(),
        name="frequency-window-detail",
    ),
    path(
        "spectrum-resources/",
        views.SpectrumResourceListView.as_view(),
        name="spectrum-resource-list",
    ),
    path(
        "spectrum-resources/<uuid:pk>/",
        views.SpectrumResourceDetailView.as_view(),
        name="spectrum-resource-detail",
    ),
    path("payload-paths/", views.PayloadPathListView.as_view(), name="payload-path-list"),
    path(
        "payload-paths/<uuid:pk>/",
        views.PayloadPathDetailView.as_view(),
        name="payload-path-detail",
    ),
    # Shared create / edit / versioning / activation routes.
    path("<str:entity>/new/", views.InventoryEditView.as_view(), name="create"),
    path("<str:entity>/<uuid:pk>/edit/", views.InventoryEditView.as_view(), name="edit"),
    # Both must precede the activation pattern below, which would otherwise capture
    # "versions" and "supersede" as its <str:action>.
    path("<str:entity>/<uuid:pk>/versions/", views.VersionHistoryView.as_view(), name="versions"),
    path("<str:entity>/<uuid:pk>/supersede/", views.SupersedeView.as_view(), name="supersede"),
    path(
        "<str:entity>/<uuid:pk>/<str:action>/",
        views.InventoryActivationView.as_view(),
        name="activation",
    ),
]
