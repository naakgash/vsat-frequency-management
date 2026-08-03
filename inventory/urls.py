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
    # Shared create / edit / activation routes.
    path("<str:entity>/new/", views.InventoryEditView.as_view(), name="create"),
    path("<str:entity>/<uuid:pk>/edit/", views.InventoryEditView.as_view(), name="edit"),
    path(
        "<str:entity>/<uuid:pk>/<str:action>/",
        views.InventoryActivationView.as_view(),
        name="activation",
    ),
]
