from django.urls import path, re_path

from satnet_paths import views

app_name = "satnet_paths"

#: The moves reachable from a URL. Constrained rather than accepting any `<str:action>`: the
#: graph decides what is *legal*, but the URL space should not offer a route to a word that is
#: not a transition at all. `approve` and `reject` are deliberately absent — they belong to the
#: approvals module, which mounts under the same prefix and records the decision (§18).
TRANSITION_ACTIONS = "plan|submit|suspend|resume|retire|cancel"

urlpatterns = [
    path("", views.SatnetPathListView.as_view(), name="list"),
    # Literal segments before `<uuid:pk>/`, the trap S5, S8 and S10 all hit.
    path("satnets/<uuid:satnet_pk>/new/", views.SatnetPathCreateView.as_view(), name="create"),
    path(
        "satnets/<uuid:satnet_pk>/auto-place/",
        views.AutoPlaceView.as_view(),
        name="auto-place",
    ),
    path("<uuid:pk>/", views.SatnetPathDetailView.as_view(), name="detail"),
    path("<uuid:pk>/edit/", views.SatnetPathEditView.as_view(), name="edit"),
    path("<uuid:pk>/revise/", views.SatnetPathReviseView.as_view(), name="revise"),
    re_path(
        rf"^(?P<pk>[0-9a-fA-F-]{{36}})/(?P<action>{TRANSITION_ACTIONS})/$",
        views.SatnetPathTransitionView.as_view(),
        name="transition",
    ),
]
