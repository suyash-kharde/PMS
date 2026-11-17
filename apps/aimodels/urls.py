from django.urls import path

from . import views

app_name = "aimodels"

urlpatterns = [
    path("ai-models/", views.provider_list, name="list"),
    path("ai-models/new/", views.provider_form, name="create"),
    path("ai-models/<int:pk>/", views.provider_detail, name="detail"),
    path("ai-models/<int:pk>/edit/", views.provider_form, name="edit"),
    path("ai-models/<int:pk>/delete/", views.provider_delete, name="delete"),
    path("ai-models/<int:pk>/default/", views.provider_make_default, name="make_default"),
    path("ai-models/<int:pk>/cert/<str:field>/delete/", views.provider_drop_cert, name="drop_cert"),
    path("api/ai-models/<int:pk>/test/", views.provider_test, name="test"),
]
