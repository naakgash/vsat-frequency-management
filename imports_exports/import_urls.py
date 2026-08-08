"""The import routes, in their own file. `docs/design/03` §6.

One module, two mounts. Export lives at `/exports/` and import at `/imports/` because they are
different actions with different capabilities — every role exports, only an administrator
imports — and a single namespace holding both would make `{% url %}` in a template say nothing
about which of the two a link is. The split follows `accounts`, which separates its own
administration routes the same way.
"""

from django.urls import path

from imports_exports import views

app_name = "imports"

urlpatterns = [
    path("", views.ImportListView.as_view(), name="list"),
    path("dry-run/", views.DryRunView.as_view(), name="dry-run"),
    path("<uuid:pk>/", views.ImportDetailView.as_view(), name="detail"),
    path("<uuid:pk>/commit/", views.CommitView.as_view(), name="commit"),
    path("<uuid:pk>/mapping/", views.RememberMappingView.as_view(), name="remember-mapping"),
]
