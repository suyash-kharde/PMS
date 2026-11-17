from django.urls import path

from . import views

app_name = "sprints"

urlpatterns = [
    # sprints
    path("sprints/", views.sprint_list, name="sprint_list"),
    path("sprints/new/", views.sprint_form, name="sprint_create"),
    path("projects/<int:project_pk>/sprints/new/", views.sprint_form, name="sprint_create_for_project"),
    path("sprints/<int:pk>/", views.sprint_detail, name="sprint_detail"),
    path("sprints/<int:pk>/edit/", views.sprint_form, name="sprint_edit"),
    path("sprints/<int:pk>/delete/", views.sprint_delete, name="sprint_delete"),
    path("sprints/<int:pk>/transition/", views.sprint_transition, name="sprint_transition"),

    # requirements
    path("projects/<int:project_pk>/requirements/", views.requirement_list, name="requirement_list"),
    path("projects/<int:project_pk>/requirements/new/", views.requirement_form, name="requirement_create_for_project"),
    path("sprints/<int:sprint_pk>/requirements/new/", views.requirement_form, name="requirement_create"),
    path("requirements/<int:pk>/", views.requirement_detail, name="requirement_detail"),
    path("requirements/<int:pk>/edit/", views.requirement_form, name="requirement_edit"),
    path("requirements/<int:pk>/delete/", views.requirement_delete, name="requirement_delete"),
    path("requirements/<int:pk>/transition/", views.requirement_transition, name="requirement_transition"),

    # inline children (kind in criteria|fields|risks|dependencies)
    path("requirements/<str:kind>/<int:requirement_pk>/add/", views.child_add, name="child_add"),
    path("requirements/items/<str:kind>/<int:pk>/edit/", views.child_edit, name="child_edit"),
    path("requirements/items/<str:kind>/<int:pk>/delete/", views.child_delete, name="child_delete"),
    path("criteria/<int:pk>/toggle/", views.criterion_toggle, name="criterion_toggle"),

    # unit test cases
    path("testcases/", views.test_case_list, name="test_case_list"),
    path("projects/<int:project_pk>/testcases/", views.test_case_list, name="test_case_list_for_project"),
    path("requirements/<int:requirement_pk>/testcases/new/", views.test_case_form, name="test_case_create"),
    path("testcases/<int:pk>/", views.test_case_detail, name="test_case_detail"),
    path("testcases/<int:pk>/edit/", views.test_case_form, name="test_case_edit"),
    path("testcases/<int:pk>/execute/", views.test_case_execute, name="test_case_execute"),
    path("testcases/<int:pk>/delete/", views.test_case_delete, name="test_case_delete"),

    # AI generation
    path("projects/<int:project_pk>/sprints/generate/", views.plan_run_start, name="plan_run_start"),
    path("sprints/plan-runs/<int:pk>/", views.plan_run_detail, name="plan_run_detail"),
    path("sprints/plan-runs/<int:pk>/cancel/", views.plan_run_cancel, name="plan_run_cancel"),
    path("sprints/plan-runs/<int:pk>/discard/", views.plan_run_discard, name="plan_run_discard"),
    path("api/sprints/plan-runs/<int:pk>/status/", views.api_plan_run_status, name="api_plan_run_status"),

    # external / serviceable services
    path("projects/<int:project_pk>/services/", views.service_list, name="service_list"),
    path("projects/<int:project_pk>/services/new/", views.service_form, name="service_create"),
    path("services/<int:pk>/edit/", views.service_form, name="service_edit"),
    path("services/<int:pk>/delete/", views.service_delete, name="service_delete"),
]
