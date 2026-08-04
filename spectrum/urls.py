from django.urls import path

from spectrum import views

app_name = "spectrum"

urlpatterns = [
    path("beams/<uuid:pk>/", views.BeamSpectrumView.as_view(), name="beam"),
]
