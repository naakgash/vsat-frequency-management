from django.urls import path, re_path

from approvals import views

app_name = "approvals"

urlpatterns = [
    path("approvals/", views.ApprovalQueueView.as_view(), name="queue"),
    # Mounted at the root so the decision sits in the Satnet Path's URL space, which is where
    # an approver is standing when they make it (`docs/design/03` §6). Constrained to the two
    # outcomes §15.2 allows, so nothing else can reach this view by inventing a word.
    re_path(
        r"^satnet-paths/(?P<pk>[0-9a-fA-F-]{36})/(?P<outcome>approve|reject)/$",
        views.ApprovalDecisionView.as_view(),
        name="decide",
    ),
]
