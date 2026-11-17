from django.urls import path

from . import views

app_name = "devlog"

urlpatterns = [
    path("projects/<int:pk>/devlog/", views.project_log, name="project_log"),
    path("projects/<int:pk>/devlog/new/", views.update_form, name="update_create"),
    path("devupdates/<int:pk>/", views.update_detail, name="update_detail"),
    path("devupdates/<int:pk>/edit/", views.update_form, name="update_edit"),
    path("devupdates/<int:pk>/delete/", views.update_delete, name="update_delete"),
]
