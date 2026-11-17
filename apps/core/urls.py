from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/tickets-by-status/", views.tickets_by_status, name="tickets_by_status"),
    path("dashboard/tickets-per-project/", views.tickets_per_project, name="tickets_per_project"),
    path("logged-in/", views.logged_in_users, name="logged_in_users"),
    path("activity/", views.activity_list, name="activity_list"),
    path("internal-projects/", views.internal_projects, name="internal_projects"),
    path("search/", views.search, name="search"),
    path("docs/", views.docs, name="docs"),
]
