from django.urls import path

from . import views

app_name = "audit"

urlpatterns = [
    path("users/<int:pk>/activity/", views.user_activity, name="user_activity"),
]
