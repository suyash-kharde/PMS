from django.urls import path

from . import views

app_name = "vms"

urlpatterns = [
    path("vms/", views.vm_list, name="vm_list"),
    path("vms/new/", views.vm_save, name="vm_create"),
    path("vms/<int:pk>/save/", views.vm_save, name="vm_save"),
    path("vms/<int:pk>/delete/", views.vm_delete, name="vm_delete"),
    path("vms/<int:vm_pk>/projects/", views.vm_projects, name="vm_projects"),
    path("vms/<int:vm_pk>/projects/<int:project_pk>/", views.vm_project_detail, name="vm_project_detail"),

    path("projects/<int:pk>/vms/", views.project_vms, name="project_vms"),
    path("projects/<int:project_pk>/vms/add/", views.project_vm_add, name="project_vm_add"),
    path(
        "projects/<int:project_pk>/vms/folder-preview/",
        views.project_vm_folder_preview,
        name="project_vm_folder_preview",
    ),
    path("projects/<int:project_pk>/vms/<int:link_pk>/save/", views.project_vm_update, name="project_vm_update"),
    path(
        "projects/<int:project_pk>/vms/<int:link_pk>/upload/",
        views.project_vm_upload_code,
        name="project_vm_upload_code",
    ),
    path("projects/<int:project_pk>/vms/<int:link_pk>/remove/", views.project_vm_remove, name="project_vm_remove"),
    path("projects/<int:project_pk>/vms/<int:link_pk>/git/save/", views.project_vm_git_save, name="project_vm_git_save"),
    path("projects/<int:project_pk>/vms/<int:link_pk>/git/pull/", views.project_vm_git_pull, name="project_vm_git_pull"),
    path("projects/<int:project_pk>/vms/<int:link_pk>/git/push/", views.project_vm_git_push, name="project_vm_git_push"),
    path(
        "projects/<int:project_pk>/vms/<int:link_pk>/jenkins/save/",
        views.project_vm_jenkins_save,
        name="project_vm_jenkins_save",
    ),
    path(
        "projects/<int:project_pk>/vms/<int:link_pk>/webhook-token/",
        views.webhook_token_generate,
        name="webhook_token_generate",
    ),

    # deployment approvals
    path("projects/<int:pk>/approvals/", views.project_approvals, name="project_approvals"),
    path("projects/<int:project_pk>/approval-steps/add/", views.approval_step_add, name="approval_step_add"),
    path(
        "projects/<int:project_pk>/approval-steps/<int:step_pk>/save/",
        views.approval_step_update,
        name="approval_step_update",
    ),
    path(
        "projects/<int:project_pk>/approval-steps/<int:step_pk>/delete/",
        views.approval_step_delete,
        name="approval_step_delete",
    ),
    path(
        "projects/<int:project_pk>/approval-steps/<int:step_pk>/move/<str:direction>/",
        views.approval_step_move,
        name="approval_step_move",
    ),
    path(
        "projects/<int:project_pk>/deployment-approvals/<int:approval_pk>/decide/",
        views.approval_decide,
        name="approval_decide",
    ),
    # NOT under /jenkins/ — that whole path prefix is already reverse-proxied straight to the
    # real Jenkins server at the infra level, in front of this app, so anything starting with
    # /jenkins/ never reaches Django at all (confirmed live: Jenkins itself answered with its
    # own CSRF-crumb error instead of a 404 from here).
    path("vms/jenkins-webhook/<str:token>/", views.jenkins_webhook, name="jenkins_webhook"),
]
