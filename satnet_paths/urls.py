from django.urls import path

from satnet_paths import views

app_name = "satnet_paths"

urlpatterns = [
    path("", views.SatnetPathListView.as_view(), name="list"),
    # Literal segments before `<uuid:pk>/`, the trap S5, S8 and S10 all hit.
    path("satnets/<uuid:satnet_pk>/new/", views.SatnetPathCreateView.as_view(), name="create"),
    path(
        "satnets/<uuid:satnet_pk>/auto-place/",
        views.AutoPlaceView.as_view(),
        name="auto-place",
    ),
    path("<uuid:pk>/", views.SatnetPathDetailView.as_view(), name="detail"),
]
