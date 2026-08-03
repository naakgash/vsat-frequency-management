"""Specification Dictionary URLs."""

from django.urls import path

from specifications import views

app_name = "specifications"

urlpatterns = [
    path("", views.SpecificationListView.as_view(), name="list"),
    path("<str:code>/", views.SpecificationDetailView.as_view(), name="detail"),
    path("<str:code>/edit/", views.SpecificationUpdateView.as_view(), name="edit"),
]
