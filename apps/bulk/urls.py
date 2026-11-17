from django.urls import path

from . import views

app_name = "bulk"

urlpatterns = [
    path("bulk/", views.home, name="home"),
    path("bulk/<str:kind>/template/", views.download_template, name="template"),
    path("bulk/<str:kind>/upload/", views.upload, name="upload"),
    path("bulk/<int:pk>/review/", views.review, name="review"),
    path("bulk/<int:pk>/confirm/", views.confirm, name="confirm"),
    path("bulk/<int:pk>/cancel/", views.cancel, name="cancel"),
    path("bulk/<int:pk>/result/", views.result, name="result"),
]
