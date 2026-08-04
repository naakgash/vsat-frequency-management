"""Calculation URLs."""

from django.urls import path

from calculations import views

app_name = "calculations"

urlpatterns = [
    path("preview/", views.PreviewView.as_view(), name="preview"),
]
