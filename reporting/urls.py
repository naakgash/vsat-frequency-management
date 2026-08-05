from django.urls import path

from reporting import views

app_name = "reporting"

urlpatterns = [
    path("satnet-paths/", views.SatnetPathTableView.as_view(), name="satnet-paths"),
    path("views/save/", views.SaveViewView.as_view(), name="save-view"),
    path("views/<uuid:pk>/delete/", views.DeleteViewView.as_view(), name="delete-view"),
]
