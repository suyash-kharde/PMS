from django.urls import path

from . import file_views, link_views, views

app_name = "projects"

urlpatterns = [
    path("customers/", views.customer_list, name="customer_list"),
    path("customers/new/", views.customer_form, name="customer_create"),
    path("customers/<int:pk>/", views.customer_detail, name="customer_detail"),
    path("customers/<int:pk>/edit/", views.customer_form, name="customer_edit"),
    path("customers/<int:pk>/delete/", views.customer_delete, name="customer_delete"),

    path("projects/", views.project_list, name="list"),
    path("projects/export/", views.project_export, name="export"),
    path("projects/new/", views.project_form, name="create"),
    path("projects/<int:pk>/", views.project_detail, name="detail"),
    path("projects/<int:pk>/edit/", views.project_form, name="edit"),
    path("projects/<int:pk>/po/", views.project_po_download, name="po_download"),
    path("projects/<int:pk>/delete/", views.project_delete, name="delete"),
    path("projects/<int:pk>/archive/", views.project_archive, name="archive"),
    path("projects/<int:pk>/milestones/new/", views.milestone_create, name="milestone_create"),
    path("milestones/<int:pk>/toggle/", views.milestone_toggle, name="milestone_toggle"),

    path("projects/<int:pk>/docs/<str:kind>/", views.project_doc_view, name="doc_view"),
    path("projects/<int:pk>/docs/<str:kind>/edit/", views.project_doc_edit, name="doc_edit"),
    path("projects/<int:pk>/docs/<str:kind>/freeze/", views.project_doc_freeze, name="doc_freeze"),
    path("projects/<int:pk>/docs/<str:kind>/download/", views.project_doc_download, name="doc_download"),
    path("projects/<int:pk>/docs/build_plan/generate/", views.project_doc_generate_plan, name="doc_generate_plan"),

    path("projects/<int:pk>/files/", file_views.project_files, name="project_files"),
    path("projects/<int:pk>/files/upload/", file_views.project_file_upload, name="project_file_upload"),
    path("projects/<int:pk>/files/<int:file_pk>/download/", file_views.project_file_download, name="project_file_download"),
    path("projects/<int:pk>/files/<int:file_pk>/delete/", file_views.project_file_delete, name="project_file_delete"),
    path("projects/<int:pk>/links/", link_views.project_links, name="project_links"),
    path("projects/<int:pk>/links/add/", link_views.project_link_save, name="project_link_add"),
    path("projects/<int:pk>/links/<int:link_pk>/save/", link_views.project_link_save, name="project_link_save"),
    path("projects/<int:pk>/links/<int:link_pk>/delete/", link_views.project_link_delete, name="project_link_delete"),
    path("projects/<int:pk>/links/<int:link_pk>/password/", link_views.project_link_password, name="project_link_password"),
    path("api/sites/auth/", link_views.site_auth, name="site_auth"),

    path("projects/<int:project_pk>/tasks/new/", views.task_form, name="task_create"),
    path("tasks/<int:pk>/", views.task_detail, name="task_detail"),
    path("tasks/<int:pk>/edit/", views.task_form, name="task_edit"),
    path("tasks/<int:pk>/delete/", views.task_delete, name="task_delete"),
    path("my-tasks/", views.my_tasks, name="my_tasks"),

    path("api/tasks/move/", views.api_task_move, name="api_task_move"),
    path("api/tasks/quick-create/", views.api_task_quick_create, name="api_task_quick_create"),
    path("api/tasks/<int:pk>/patch/", views.api_task_patch, name="api_task_patch"),
    path("api/projects/<int:pk>/stats/", views.api_project_stats, name="api_project_stats"),
]
