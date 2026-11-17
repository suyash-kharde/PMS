from django.urls import path

from . import views

app_name = "notify"

urlpatterns = [
    path("notifications/", views.inbox, name="inbox"),
    path("notifications/read/", views.mark_read, name="mark_all_read"),
    path("notifications/<int:pk>/read/", views.mark_read, name="mark_read"),
    path("api/notifications/unread/", views.unread_feed, name="unread_feed"),
    path("api/push/public-key/", views.public_key, name="public_key"),
    path("api/push/subscribe/", views.subscribe, name="subscribe"),
    path("api/push/unsubscribe/", views.unsubscribe, name="unsubscribe"),
    path("api/push/test/", views.send_test, name="send_test"),
]
