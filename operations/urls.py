"""Health endpoint URLs, mounted at /health/ by the root URL configuration."""

from django.urls import path

from operations import views

urlpatterns = [
    path("live", views.health_live, name="health-live"),
    path("ready", views.health_ready, name="health-ready"),
]
