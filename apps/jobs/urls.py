from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("ai-queue/", views.queue_page, name="queue"),
    path("ai-queue/<int:pk>/cancel/", views.job_cancel, name="cancel"),
    path("ai-queue/<int:pk>/retry/", views.job_retry, name="retry"),
    path("api/jobs/<int:pk>/", views.api_job, name="api_job"),
]
