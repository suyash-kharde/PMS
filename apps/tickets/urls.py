from django.urls import path

from . import views

app_name = "tickets"

urlpatterns = [
    path("tickets/", views.ticket_list, name="list"),
    path("tickets/new/", views.ticket_form, name="create"),
    path("tickets/mine/", views.my_tickets, name="mine"),
    path("projects/<int:project_pk>/tickets/new/", views.ticket_form, name="create_for_project"),

    path("tickets/<int:pk>/", views.ticket_detail, name="detail"),
    path("tickets/<int:pk>/log/", views.ticket_log, name="log"),
    path("tickets/<int:pk>/edit/", views.ticket_form, name="edit"),
    path("tickets/<int:pk>/delete/", views.ticket_delete, name="delete"),
    path("tickets/<int:pk>/transition/", views.ticket_transition, name="transition"),
    path("tickets/<int:pk>/watch/", views.ticket_watch, name="watch"),
    path("tickets/<int:pk>/convert/", views.ticket_convert_to_task, name="convert"),
    path("tickets/<int:pk>/attachments/", views.ticket_attachment_upload, name="attach"),
    path("tickets/attachments/<int:pk>/delete/", views.ticket_attachment_delete, name="attach_delete"),

    path("api/tickets/<int:pk>/patch/", views.api_ticket_patch, name="api_patch"),
]
