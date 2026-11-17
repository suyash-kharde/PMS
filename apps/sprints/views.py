from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.accounts.permissions import has_cap, require
from apps.notify import services as notify
from apps.projects.models import Activity
from apps.projects.selectors import visible_projects

from .forms import (
    AcceptanceCriterionForm,
    DependencyForm,
    ExternalServiceForm,
    FieldSpecForm,
    RequirementForm,
    RequirementRiskForm,
    SprintForm,
    TestExecutionForm,
    UnitTestCaseForm,
)
from .models import (
    AcceptanceCriterion,
    Dependency,
    ExternalService,
    FieldSpec,
    Requirement,
    RequirementRisk,
    REQUIREMENT_STATUS,
    Sprint,
    SprintPlanRun,
    SPRINT_STATUS,
    TEST_STATUS,
    UnitTestCase,
)
from .selectors import visible_requirements, visible_services, visible_sprints, visible_test_cases

WIDE_FIELDS = {
    "description", "mitigation", "statement", "given_text", "when_text", "then_text",
    "notes", "steps", "expected_result", "preconditions", "input_data", "actual_result",
    "comments", "goal", "user_story", "details",
}

# child model -> (form class, related_name on Requirement, human label)
CHILD_CONFIG = {
    "criteria": (AcceptanceCriterionForm, AcceptanceCriterion, "acceptance criterion"),
    "fields": (FieldSpecForm, FieldSpec, "field specification"),
    "risks": (RequirementRiskForm, RequirementRisk, "risk"),
    "dependencies": (DependencyForm, Dependency, "dependency"),
}


# --------------------------------------------------------------------------- helpers
def _get_sprint(user, pk):
    sprint = visible_sprints(user).filter(pk=pk).first()
    if not sprint:
        raise Http404("Sprint not found")
    return sprint


def _get_requirement(user, pk):
    requirement = visible_requirements(user).filter(pk=pk).first()
    if not requirement:
        raise Http404("Requirement not found")
    return requirement


def _get_test_case(user, pk):
    test_case = visible_test_cases(user).filter(pk=pk).first()
    if not test_case:
        raise Http404("Test case not found")
    return test_case


def _get_project(user, pk):
    return get_object_or_404(visible_projects(user), pk=pk)


def _can_manage(user, project):
    return has_cap(user, "sprint.manage", project)


def _require_manage(user, project):
    if not _can_manage(user, project):
        raise PermissionDenied("Missing capability: sprint.manage")


def _child_form_kwargs(form_cls, requirement):
    if form_cls is DependencyForm:
        return {"requirement": requirement}
    if form_cls is RequirementRiskForm:
        return {"project": requirement.project}
    return {}


# --------------------------------------------------------------------------- sprints
@login_required
def sprint_list(request):
    require(request.user, "sprint.view")
    sprints = visible_sprints(request.user).annotate(n_req=Count("requirements"))

    status = request.GET.get("status", "")
    project_id = request.GET.get("project", "")
    q = request.GET.get("q", "").strip()

    if status:
        sprints = sprints.filter(status=status)
    if q:
        sprints = sprints.filter(Q(name__icontains=q) | Q(goal__icontains=q))

    project = None
    if project_id:
        project = visible_projects(request.user).filter(pk=project_id).first()
        if project:
            sprints = sprints.filter(project=project)

    context = {
        "sprints": sprints,
        "project": project,
        "projects": visible_projects(request.user).filter(archived=False),
        "f_status": status,
        "f_project": project_id,
        "q": q,
        "status_choices": SPRINT_STATUS,
        "can_manage": has_cap(request.user, "sprint.manage", project) if project else has_cap(request.user, "sprint.manage"),
        "backlog_count": visible_requirements(request.user).filter(
            sprint__isnull=True, **({"project": project} if project else {})
        ).exclude(status__in=["done", "deferred"]).count(),
        "can_generate": has_cap(request.user, "sprint.generate", project) if project else False,
        "plan_runs": project.plan_runs.all()[:5] if project else None,
        "tab": "sprints" if project else None,
    }
    return render(request, "sprints/sprint_list.html", context)


@login_required
def sprint_detail(request, pk):
    sprint = _get_sprint(request.user, pk)
    requirements = sprint.requirements.select_related("assignee").prefetch_related("criteria", "test_cases")
    buckets = {key: [] for key, _ in REQUIREMENT_STATUS}
    for requirement in requirements:
        buckets.setdefault(requirement.status, []).append(requirement)

    risks = RequirementRisk.objects.filter(requirement__sprint=sprint).select_related("requirement", "owner")
    dependencies = Dependency.objects.filter(requirement__sprint=sprint).select_related("requirement", "depends_on")

    context = {
        "sprint": sprint,
        "project": sprint.project,
        "columns": [
            {"key": key, "label": label, "rows": buckets.get(key, [])}
            for key, label in REQUIREMENT_STATUS
        ],
        "risks": risks,
        "dependencies": dependencies,
        "open_dependencies": [d for d in dependencies if d.status != "resolved"],
        "can_manage": _can_manage(request.user, sprint.project),
        "status_choices": SPRINT_STATUS,
        "tab": "sprints",
    }
    return render(request, "sprints/sprint_detail.html", context)


@login_required
def sprint_form(request, pk=None, project_pk=None):
    if pk:
        sprint = _get_sprint(request.user, pk)
        project = sprint.project
        _require_manage(request.user, project)
    else:
        sprint = None
        project = _get_project(request.user, project_pk) if project_pk else None
        if project:
            _require_manage(request.user, project)
        elif not has_cap(request.user, "sprint.manage"):
            raise PermissionDenied("Missing capability: sprint.manage")

    if request.method == "POST" and project is None and sprint is None:
        project = get_object_or_404(visible_projects(request.user), pk=request.POST.get("project"))
        _require_manage(request.user, project)

    form = SprintForm(request.POST or None, instance=sprint, project=project)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        creating = obj.pk is None
        if creating:
            obj.project = project
        obj.save()
        form.save_m2m()
        Activity.log(request.user, f"{'created' if creating else 'updated'} sprint “{obj.name}”", project=obj.project)
        messages.success(request, f"Sprint {obj.key} {'created' if creating else 'updated'}.")
        return redirect(obj.get_absolute_url())

    return render(
        request,
        "sprints/sprint_form.html",
        {
            "form": form,
            "sprint": sprint,
            "project": project,
            "projects": visible_projects(request.user).filter(archived=False),
            "wide_fields": WIDE_FIELDS,
        },
    )


@login_required
@require_POST
def sprint_delete(request, pk):
    sprint = _get_sprint(request.user, pk)
    _require_manage(request.user, sprint.project)
    key, project = sprint.key, sprint.project
    sprint.delete()
    messages.success(request, f"Sprint {key} deleted.")
    return redirect(f"{reverse('sprints:sprint_list')}?project={project.pk}")


@login_required
@require_POST
def sprint_transition(request, pk):
    sprint = _get_sprint(request.user, pk)
    _require_manage(request.user, sprint.project)
    target = request.POST.get("status", "")
    if target not in dict(SPRINT_STATUS):
        raise Http404("Unknown status")
    old = sprint.get_status_display()
    sprint.status = target
    sprint.save(update_fields=["status", "updated_at"])
    Activity.log(request.user, f"moved sprint {sprint.key} from {old} to {sprint.get_status_display()}", project=sprint.project)
    messages.success(request, f"{sprint.key} is now {sprint.get_status_display()}.")
    return redirect(sprint.get_absolute_url())


# --------------------------------------------------------------------------- requirements
@login_required
def requirement_list(request, project_pk):
    project = _get_project(request.user, project_pk)
    require(request.user, "sprint.view")
    requirements = visible_requirements(request.user).filter(project=project)

    status = request.GET.get("status", "open")
    if status == "open":
        requirements = requirements.exclude(status__in=["done", "deferred"])
    elif status and status != "all":
        requirements = requirements.filter(status=status)

    sprints = visible_sprints(request.user).filter(project=project)
    grouped = [{"sprint": s, "rows": [r for r in requirements if r.sprint_id == s.pk]} for s in sprints]
    backlog = [r for r in requirements if r.sprint_id is None]

    context = {
        "project": project,
        "grouped": grouped,
        "backlog": backlog,
        "f_status": status,
        "status_choices": REQUIREMENT_STATUS,
        "can_manage": _can_manage(request.user, project),
        "tab": "sprints",
    }
    return render(request, "sprints/requirement_list.html", context)


@login_required
def requirement_detail(request, pk):
    requirement = _get_requirement(request.user, pk)
    can_manage = _can_manage(request.user, requirement.project)
    context = {
        "requirement": requirement,
        "project": requirement.project,
        "sprint": requirement.sprint,
        "criteria": requirement.criteria.all(),
        "field_specs": requirement.field_specs.all(),
        "risks": requirement.risks.select_related("owner"),
        "dependencies": requirement.dependencies.select_related("depends_on", "owner"),
        "test_cases": requirement.test_cases.select_related("developer"),
        "services": requirement.external_services.all(),
        "can_manage": can_manage,
        "can_test": has_cap(request.user, "testcase.manage", requirement.project),
        "criterion_form": AcceptanceCriterionForm(),
        "field_form": FieldSpecForm(),
        "risk_form": RequirementRiskForm(project=requirement.project),
        "dependency_form": DependencyForm(requirement=requirement),
        "status_choices": REQUIREMENT_STATUS,
    }
    return render(request, "sprints/requirement_detail.html", context)


@login_required
def requirement_form(request, pk=None, sprint_pk=None, project_pk=None):
    sprint = None
    if pk:
        requirement = _get_requirement(request.user, pk)
        project = requirement.project
    else:
        requirement = None
        if sprint_pk:
            sprint = _get_sprint(request.user, sprint_pk)
            project = sprint.project
        else:
            project = _get_project(request.user, project_pk)
    _require_manage(request.user, project)

    form = RequirementForm(request.POST or None, instance=requirement, project=project)
    if requirement is None and sprint is not None:
        form.fields["sprint"].initial = sprint

    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        creating = obj.pk is None
        old_assignee = None if creating else Requirement.objects.get(pk=obj.pk).assignee_id
        if creating:
            obj.project = project
            obj.reporter = request.user
            if sprint and not obj.sprint_id:
                obj.sprint = sprint
        obj.save()
        form.save_m2m()
        Activity.log(
            request.user,
            f"{'created' if creating else 'updated'} requirement “{obj.title}”",
            project=obj.project,
        )
        if obj.assignee_id and obj.assignee_id != old_assignee and obj.assignee_id != request.user.pk:
            notify.requirement_assigned(obj, request.user)
        messages.success(request, f"Requirement {obj.key} {'created' if creating else 'updated'}.")
        return redirect(obj.get_absolute_url())

    return render(
        request,
        "sprints/requirement_form.html",
        {"form": form, "requirement": requirement, "project": project, "sprint": sprint, "wide_fields": WIDE_FIELDS},
    )


@login_required
@require_POST
def requirement_delete(request, pk):
    requirement = _get_requirement(request.user, pk)
    _require_manage(request.user, requirement.project)
    key, target = requirement.key, requirement.sprint
    requirement.delete()
    messages.success(request, f"Requirement {key} deleted.")
    return redirect(target.get_absolute_url() if target else "sprints:sprint_list")


@login_required
@require_POST
def requirement_transition(request, pk):
    requirement = _get_requirement(request.user, pk)
    _require_manage(request.user, requirement.project)
    target = request.POST.get("status", "")
    if target not in dict(REQUIREMENT_STATUS):
        raise Http404("Unknown status")
    old = requirement.get_status_display()
    requirement.status = target
    requirement.save(update_fields=["status", "updated_at"])
    Activity.log(
        request.user,
        f"moved requirement {requirement.key} from {old} to {requirement.get_status_display()}",
        project=requirement.project,
    )
    return redirect(requirement.get_absolute_url())


# --------------------------------------------------------------------------- inline children
@login_required
@require_POST
def child_add(request, kind, requirement_pk):
    if kind not in CHILD_CONFIG:
        raise Http404("Unknown item type")
    requirement = _get_requirement(request.user, requirement_pk)
    _require_manage(request.user, requirement.project)
    form_cls, model, label = CHILD_CONFIG[kind]
    form = form_cls(request.POST, **_child_form_kwargs(form_cls, requirement))
    if form.is_valid():
        obj = form.save(commit=False)
        obj.requirement = requirement
        obj.save()
        form.save_m2m()
        Activity.log(request.user, f"added a {label} to {requirement.key}", project=requirement.project)
        messages.success(request, f"Added {label}.")
    else:
        messages.error(request, f"Could not add {label}: {form.errors.as_text()}")
    return redirect(requirement.get_absolute_url())


def _get_child(request, kind, pk):
    form_cls, model, label = CHILD_CONFIG[kind]
    obj = get_object_or_404(model.objects.select_related("requirement__project"), pk=pk)
    requirement = _get_requirement(request.user, obj.requirement_id)
    _require_manage(request.user, requirement.project)
    return obj, requirement, form_cls, label


@login_required
def child_edit(request, kind, pk):
    if kind not in CHILD_CONFIG:
        raise Http404("Unknown item type")
    obj, requirement, form_cls, label = _get_child(request, kind, pk)
    form = form_cls(request.POST or None, instance=obj, **_child_form_kwargs(form_cls, requirement))
    if request.method == "POST" and form.is_valid():
        form.save()
        Activity.log(request.user, f"edited a {label} on {requirement.key}", project=requirement.project)
        messages.success(request, f"{label.capitalize()} updated.")
        return redirect(requirement.get_absolute_url())
    return render(
        request,
        "sprints/_object_form.html",
        {
            "form": form,
            "title": f"Edit {label}",
            "requirement": requirement,
            "cancel_url": requirement.get_absolute_url(),
            "wide_fields": WIDE_FIELDS,
        },
    )


@login_required
@require_POST
def child_delete(request, kind, pk):
    if kind not in CHILD_CONFIG:
        raise Http404("Unknown item type")
    obj, requirement, _form_cls, label = _get_child(request, kind, pk)
    obj.delete()
    Activity.log(request.user, f"removed a {label} from {requirement.key}", project=requirement.project)
    messages.success(request, f"{label.capitalize()} removed.")
    return redirect(requirement.get_absolute_url())


@login_required
@require_POST
def criterion_toggle(request, pk):
    criterion = get_object_or_404(AcceptanceCriterion.objects.select_related("requirement__project"), pk=pk)
    requirement = _get_requirement(request.user, criterion.requirement_id)
    _require_manage(request.user, requirement.project)
    criterion.is_met = not criterion.is_met
    criterion.save(update_fields=["is_met"])
    return redirect(requirement.get_absolute_url())


# --------------------------------------------------------------------------- unit test cases
@login_required
def test_case_list(request, project_pk=None):
    require(request.user, "sprint.view")
    test_cases = visible_test_cases(request.user)
    project = None
    if project_pk:
        project = _get_project(request.user, project_pk)
        test_cases = test_cases.filter(project=project)

    status = request.GET.get("status", "")
    test_type = request.GET.get("type", "")
    if status:
        test_cases = test_cases.filter(status=status)
    if test_type:
        test_cases = test_cases.filter(test_type=test_type)

    context = {
        "test_cases": test_cases,
        "project": project,
        "projects": visible_projects(request.user).filter(archived=False),
        "f_status": status,
        "f_type": test_type,
        "status_choices": TEST_STATUS,
        "type_choices": UnitTestCase._meta.get_field("test_type").choices,
        "can_test": has_cap(request.user, "testcase.manage", project) if project else has_cap(request.user, "testcase.manage"),
        "tab": "sprints" if project else None,
    }
    return render(request, "sprints/test_case_list.html", context)


@login_required
def test_case_detail(request, pk):
    test_case = _get_test_case(request.user, pk)
    context = {
        "tc": test_case,
        "project": test_case.project,
        "can_test": has_cap(request.user, "testcase.manage", test_case.project),
    }
    return render(request, "sprints/test_case_detail.html", context)


@login_required
def test_case_form(request, pk=None, requirement_pk=None):
    requirement = None
    if pk:
        test_case = _get_test_case(request.user, pk)
        project = test_case.project
        requirement = test_case.requirement
    else:
        test_case = None
        requirement = _get_requirement(request.user, requirement_pk)
        project = requirement.project
    if not has_cap(request.user, "testcase.manage", project):
        raise PermissionDenied("Missing capability: testcase.manage")

    form = UnitTestCaseForm(
        request.POST or None, instance=test_case, project=project, requirement=requirement
    )
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        creating = obj.pk is None
        if creating:
            obj.project = project
        if requirement and not obj.requirement_id:
            obj.requirement = requirement
        obj.save()
        Activity.log(
            request.user,
            f"{'wrote' if creating else 'updated'} unit test {obj.key}",
            project=project,
        )
        messages.success(request, f"Test case {obj.key} {'created' if creating else 'updated'}.")
        return redirect(obj.get_absolute_url())

    return render(
        request,
        "sprints/test_case_form.html",
        {"form": form, "tc": test_case, "project": project, "requirement": requirement, "wide_fields": WIDE_FIELDS},
    )


@login_required
def test_case_execute(request, pk):
    test_case = _get_test_case(request.user, pk)
    if not has_cap(request.user, "testcase.manage", test_case.project):
        raise PermissionDenied("Missing capability: testcase.manage")

    form = TestExecutionForm(request.POST or None, instance=test_case)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.developer = request.user
        if not obj.executed_on:
            obj.executed_on = timezone.localdate()
        obj.save(update_fields=["status", "actual_result", "executed_on", "comments", "developer", "updated_at"])
        Activity.log(
            request.user, f"recorded {obj.get_status_display()} for unit test {obj.key}", project=obj.project
        )
        if obj.status in ("fail", "blocked"):
            notify.test_case_result(obj, request.user)
        messages.success(request, f"{obj.key} recorded as {obj.get_status_display()}.")
        return redirect(obj.get_absolute_url())

    return render(
        request,
        "sprints/test_case_execute.html",
        {"form": form, "tc": test_case, "project": test_case.project, "wide_fields": WIDE_FIELDS},
    )


@login_required
@require_POST
def test_case_delete(request, pk):
    test_case = _get_test_case(request.user, pk)
    if not has_cap(request.user, "testcase.manage", test_case.project):
        raise PermissionDenied("Missing capability: testcase.manage")
    key, requirement = test_case.key, test_case.requirement
    test_case.delete()
    messages.success(request, f"Test case {key} deleted.")
    return redirect(requirement.get_absolute_url() if requirement else "sprints:test_case_list")


# --------------------------------------------------------------------------- external services
@login_required
def service_list(request, project_pk):
    project = _get_project(request.user, project_pk)
    require(request.user, "sprint.view")
    services = project.external_services.select_related("owner").prefetch_related("requirements")

    provided_by = request.GET.get("provided_by", "")
    status = request.GET.get("status", "")
    if provided_by:
        services = services.filter(provided_by=provided_by)
    if status:
        services = services.filter(status=status)

    context = {
        "project": project,
        "services": services,
        "f_provided_by": provided_by,
        "f_status": status,
        "provider_choices": ExternalService._meta.get_field("provided_by").choices,
        "status_choices": ExternalService._meta.get_field("status").choices,
        "outstanding": services.exclude(status__in=["configured", "verified"]).count(),
        "can_manage": has_cap(request.user, "service.manage", project),
        "tab": "services",
    }
    return render(request, "sprints/service_list.html", context)


@login_required
def service_form(request, project_pk=None, pk=None):
    if pk:
        service = get_object_or_404(ExternalService.objects.select_related("project"), pk=pk)
        project = _get_project(request.user, service.project_id)
    else:
        service = None
        project = _get_project(request.user, project_pk)
    if not has_cap(request.user, "service.manage", project):
        raise PermissionDenied("Missing capability: service.manage")

    form = ExternalServiceForm(request.POST or None, instance=service, project=project)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        creating = obj.pk is None
        if creating:
            obj.project = project
        obj.save()
        form.save_m2m()
        Activity.log(
            request.user,
            f"{'added' if creating else 'updated'} external service “{obj.name}”",
            project=project,
        )
        messages.success(request, f"Service “{obj.name}” {'added' if creating else 'updated'}.")
        return redirect("sprints:service_list", project.pk)

    return render(
        request,
        "sprints/service_form.html",
        {"form": form, "service": service, "project": project, "wide_fields": WIDE_FIELDS},
    )


@login_required
@require_POST
def service_delete(request, pk):
    service = get_object_or_404(ExternalService.objects.select_related("project"), pk=pk)
    project = _get_project(request.user, service.project_id)
    if not has_cap(request.user, "service.manage", project):
        raise PermissionDenied("Missing capability: service.manage")
    name = service.name
    service.delete()
    messages.success(request, f"Service “{name}” removed.")
    return redirect("sprints:service_list", project.pk)


# --------------------------------------------------------------------------- AI generation
def _get_plan_run(user, pk):
    run = (
        SprintPlanRun.objects.select_related("project", "requested_by")
        .filter(pk=pk, project__in=visible_projects(user))
        .first()
    )
    if not run:
        raise Http404("Run not found")
    return run


@login_required
def plan_run_start(request, project_pk):
    project = _get_project(request.user, project_pk)
    if not has_cap(request.user, "sprint.generate", project):
        raise PermissionDenied("Missing capability: sprint.generate")

    from .services import sources as sources_mod

    active = project.plan_runs.exclude(status__in=["done", "failed", "cancelled"]).first()

    if request.method == "POST":
        if active:
            messages.info(request, "A generation run is already in progress.")
            return redirect(active.get_absolute_url())
        options = {
            "sprint_weeks": max(1, min(8, int(request.POST.get("sprint_weeks") or 2))),
            "target_sprints": int(request.POST["target_sprints"]) if request.POST.get("target_sprints") else None,
        }
        run = SprintPlanRun.objects.create(
            project=project, requested_by=request.user, options=options, status="queued"
        )
        Activity.log(request.user, "started an AI sprint plan generation", project=project)
        from apps.jobs.handlers import queue_sprint_plan
        from apps.jobs.services import queued_message

        job, created = queue_sprint_plan(run, request.user)
        messages.success(request, queued_message(job, created))
        return redirect(run.get_absolute_url())

    collected = sources_mod.collect(project)
    return render(
        request,
        "sprints/plan_run_start.html",
        {
            "project": project,
            "active_run": active,
            "recent_runs": project.plan_runs.all()[:5],
            "meta": collected["meta"],
            "quality": sources_mod.source_quality(collected["meta"]),
            "tab": "sprints",
        },
    )


@login_required
def plan_run_detail(request, pk):
    run = _get_plan_run(request.user, pk)
    return render(
        request,
        "sprints/plan_run_detail.html",
        {
            "run": run,
            "project": run.project,
            "steps": run.steps.all(),
            "can_discard": has_cap(request.user, "sprint.manage", run.project),
            "tab": "sprints",
        },
    )


@login_required
def api_plan_run_status(request, pk):
    run = _get_plan_run(request.user, pk)
    return JsonResponse(
        {
            "status": run.status,
            "status_label": run.get_status_display(),
            "phase": run.phase,
            "terminal": run.is_terminal,
            "duration": run.duration,
            "summary": run.summary,
            "error": run.error,
            "result": run.result or {},
            "log": run.log[-4000:],
            "steps": [
                {
                    "key": s.key,
                    "label": s.label,
                    "status": s.status,
                    "detail": s.detail,
                }
                for s in run.steps.all()
            ],
        }
    )


@login_required
@require_POST
def plan_run_cancel(request, pk):
    run = _get_plan_run(request.user, pk)
    if not has_cap(request.user, "sprint.generate", run.project):
        raise PermissionDenied("Missing capability: sprint.generate")
    if not run.is_terminal:
        run.status = "cancelled"
        run.save(update_fields=["status"])
        messages.info(request, "Cancelling — the worker will stop at the next step boundary.")
    return redirect(run.get_absolute_url())


@login_required
@require_POST
def plan_run_discard(request, pk):
    run = _get_plan_run(request.user, pk)
    if not has_cap(request.user, "sprint.manage", run.project):
        raise PermissionDenied("Missing capability: sprint.manage")
    if not run.is_terminal:
        messages.error(request, "Cancel the run before discarding it.")
        return redirect(run.get_absolute_url())

    project = run.project
    # children (criteria, field specs, risks, dependencies) cascade from Requirement
    Requirement.objects.filter(generated_by=run).delete()
    Sprint.objects.filter(generated_by=run).delete()
    UnitTestCase.objects.filter(generated_by=run).delete()
    ExternalService.objects.filter(generated_by=run).delete()
    run.delete()
    Activity.log(request.user, "discarded an AI-generated sprint plan", project=project)
    messages.success(request, "Generated sprints, requirements and services removed.")
    return redirect(f"{reverse('sprints:sprint_list')}?project={project.pk}")
