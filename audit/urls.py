"""Audit routes. §18, `docs/design/03` §6.

**Every one of these is a GET, and that is a property rather than a coincidence.** An audit
event cannot be created, changed or deleted through the application by anyone, including an
administrator (`docs/design/03` §2.1), so there is no form, no POST target and no delete route
to protect. `tests/audit/test_audit_ui.py` enumerates this module and fails on any pattern whose
view accepts an unsafe method.
"""

from django.urls import path

from audit import views

app_name = "audit"

urlpatterns = [
    path("", views.AuditSearchView.as_view(), name="search"),
    path("<uuid:pk>/", views.AuditEventView.as_view(), name="event"),
    # `str` rather than `slug` for the type: an object type is `app_label.ModelName`, and a
    # slug converter would refuse the dot.
    path(
        "history/<str:object_type>/<uuid:object_id>/",
        views.ObjectHistoryView.as_view(),
        name="history",
    ),
]
