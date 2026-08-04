from django.urls import path

from satnets import views

app_name = "satnets"

urlpatterns = [
    path("", views.SatnetListView.as_view(), name="list"),
    path("new/", views.SatnetCreateView.as_view(), name="create"),
    # Before `<uuid:pk>/` so the literal segments are not captured by it — the trap S5 and S8
    # both hit, and it now carries a comment in all three places.
    path("<uuid:pk>/edit/", views.SatnetEditView.as_view(), name="edit"),
    path("<uuid:pk>/activation/", views.SatnetActivationView.as_view(), name="activation"),
    path("<uuid:pk>/", views.SatnetDetailView.as_view(), name="detail"),
]
