from django.urls import path

from . import views

app_name = "intake"

urlpatterns = [
    path("projects/<int:pk>/timeline/", views.timeline, name="timeline"),
    path("projects/<int:pk>/timeline/add/", views.entry_create, name="entry_create"),
    path("entries/<int:pk>/", views.entry_detail, name="entry_detail"),
    path("entries/<int:pk>/reprocess/", views.entry_reprocess, name="entry_reprocess"),
    path("entries/<int:pk>/delete/", views.entry_delete, name="entry_delete"),
    path("entries/<int:pk>/speakers/", views.speakers_save, name="speakers_save"),
    path("entries/<int:pk>/clip/", views.entry_clip, name="entry_clip"),
    path("entries/<int:pk>/transcript/", views.transcript_edit, name="transcript_edit"),
    path("entries/<int:pk>/sync/", views.entry_sync_brd, name="entry_sync_brd"),
    path("api/entries/<int:pk>/status/", views.api_entry_status, name="api_entry_status"),
    path("api/speakers/<int:pk>/", views.api_speaker_patch, name="api_speaker_patch"),

    path("projects/<int:pk>/participants/", views.participants, name="participants"),
    path("projects/<int:pk>/participants/save/", views.participant_save, name="participant_create"),
    path("projects/<int:pk>/participants/new-person/", views.participant_add_person, name="participant_add_person"),
    path("projects/<int:pk>/participants/<int:membership_pk>/save/", views.participant_save, name="participant_save"),
    path("projects/<int:pk>/participants/<int:membership_pk>/delete/", views.participant_delete, name="participant_delete"),
    path("projects/<int:pk>/participants/<int:membership_pk>/move/", views.participant_move, name="participant_move"),

    path("projects/<int:pk>/brd/", views.brd_view, name="brd"),
    path("projects/<int:pk>/brd/freeze/", views.brd_freeze, name="brd_freeze"),
    path("projects/<int:pk>/brd/regenerate/", views.brd_regenerate, name="brd_regenerate"),
    path("projects/<int:pk>/brd/edit/", views.brd_edit, name="brd_edit"),
    path("projects/<int:pk>/brd/delete/", views.brd_delete, name="brd_delete"),
    path("projects/<int:pk>/brd/history/", views.brd_history, name="brd_history"),
    path("projects/<int:pk>/brd/download/", views.brd_download, name="brd_download"),
    path("projects/<int:pk>/brd/download/v<int:number>/", views.brd_download, name="brd_download_version"),

    path("service-status/", views.service_status, name="service_status"),
]
