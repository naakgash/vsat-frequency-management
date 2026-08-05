from django.urls import path

from imports_exports import views

app_name = "exports"

urlpatterns = [
    path("satnet-paths.xlsx", views.SatnetPathExportView.as_view(), name="satnet-paths"),
]
