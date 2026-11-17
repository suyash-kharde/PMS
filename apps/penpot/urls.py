from django.urls import path

from . import views

app_name = "penpot"

urlpatterns = [
    path("projects/<int:pk>/design/", views.manage, name="manage"),
    path("projects/<int:pk>/design/provision/", views.provision_now, name="provision"),
    path("projects/<int:pk>/design/rebuild/", views.rebuild_now, name="rebuild"),
    path("projects/<int:pk>/design/sync/", views.sync_now, name="sync"),
    path("projects/<int:pk>/design/share/", views.share_create, name="share_create"),
    path("projects/<int:pk>/design/share/revoke/", views.share_revoke, name="share_revoke"),
    path("projects/<int:pk>/design/invite/", views.invite_create, name="invite_create"),
    path("projects/<int:pk>/design/disable/", views.disable_workspace, name="disable"),
]
