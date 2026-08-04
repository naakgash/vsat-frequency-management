"""Beam URLs."""

from django.urls import path

from beams import views

app_name = "beams"

urlpatterns = [
    path("", views.BeamListView.as_view(), name="list"),
    path("new/", views.BeamCreateView.as_view(), name="builder-create"),
    path("<uuid:pk>/", views.BeamDetailView.as_view(), name="detail"),
    # Steps 4 and 5 must precede the direction route below, which would otherwise capture
    # "validate" and "activate" as its <str:direction> and 404 on an unknown direction.
    # The same ordering trap as inventory's activation route in S5 — a named segment and a
    # free one in the same position always resolve top-down.
    path("<uuid:pk>/build/validate/", views.BeamValidateView.as_view(), name="builder-validate"),
    path("<uuid:pk>/build/activate/", views.BeamActivationView.as_view(), name="builder-activate"),
    # Steps 2 and 3 share a route because the two chains are structurally identical; the
    # direction is the only thing that differs.
    path(
        "<uuid:pk>/build/<str:direction>/",
        views.BeamDirectionView.as_view(),
        name="builder-direction",
    ),
]
