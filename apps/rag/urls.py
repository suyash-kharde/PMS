from django.urls import path

from . import views

app_name = "rag"

urlpatterns = [
    path("projects/<int:pk>/chat/", views.chat, name="chat"),
    path("projects/<int:pk>/chat/reindex/", views.reindex, name="reindex"),
    path("projects/<int:pk>/chat/clear/", views.clear_chat, name="clear_chat"),
]
