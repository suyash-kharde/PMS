import json
import os
from datetime import timedelta

import markdown as md
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.db.models.functions import Lower
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.accounts.permissions import has_cap, require
from apps.audit.services import log as audit_log

from . import exports
from .forms import CommentForm, CustomerForm, MilestoneForm, ProjectDocumentForm, ProjectForm, TaskForm
from .models import (
    DOC_DEFAULT_CONTENT, DOC_KINDS, Activity, Customer, Milestone, Project, ProjectDocument, ProjectMembership,
    Task, WorkflowState,
)
from .selectors import visible_customers, visible_projects, visible_tasks

MD_EXTENSIONS = ["extra", "sane_lists", "toc", "nl2br"]
DOC_KIND_SLUGS = {slug for slug, _ in DOC_KINDS}

# "newly added" quick filters for the Projects / Customers list pages — days since created_at.
CREATED_PRESETS = [("7", "Last 7 days"), ("30", "Last 30 days"), ("90", "Last 90 days"), ("365", "This year")]


def render_markdown(text):
    return md.markdown(text or "", extensions=MD_EXTENSIONS)


def _filter_by_created(qs, request):
    """Apply the optional "created" preset / custom date range filter shared by
    project_list and customer_list. A no-op when none of these GET params are set,
    so it never changes behaviour for existing links/bookmarks without them."""
    date_from = parse_date(request.GET.get("date_from", "").strip())
    date_to = parse_date(request.GET.get("date_to", "").strip())
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if not date_from and not date_to:
        days = dict(CREATED_PRESETS).keys()
        preset = request.GET.get("created", "")
        if preset in days:
            qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=int(preset)))
    return qs


def _get_project(user, pk):
    project = visible_projects(user).filter(pk=pk).first()
    if not project:
        raise Http404("Project not found")
    return project


def _get_task(user, pk):
    task = visible_tasks(user).filter(pk=pk).first()
    if not task:
        raise Http404("Task not found")
    return task


# --------------------------------------------------------------------------- customers
@login_required
def customer_list(request):
    require(request.user, "customer.view")
    customers = visible_customers(request.user)
    q = request.GET.get("q", "").strip()
    if q:
        customers = customers.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(industry__icontains=q))
    customers = _filter_by_created(customers, request)

    projects = visible_projects(request.user).filter(archived=False)
    by_customer = {}
    for project in projects:
        by_customer.setdefault(project.customer_id, []).append(project)

    rows = [
        {"customer": customer, "projects": by_customer.get(customer.pk, []),
         "stats": customer.stats(by_customer.get(customer.pk, []))}
        for customer in customers
    ]
    return render(
        request,
        "projects/customer_list.html",
        {
            "rows": rows,
            "q": q,
            "internal": by_customer.get(None, []),
            "can_manage": has_cap(request.user, "customer.manage"),
            "created": request.GET.get("created", ""),
            "date_from": request.GET.get("date_from", ""),
            "date_to": request.GET.get("date_to", ""),
            "created_presets": CREATED_PRESETS,
        },
    )


@login_required
def customer_detail(request, pk):
    require(request.user, "customer.view")
    customer = get_object_or_404(visible_customers(request.user), pk=pk)
    projects = visible_projects(request.user).filter(customer=customer)
    # only people on the projects this person can see — not everyone on the customer's other projects
    people = User.objects.filter(memberships__project__in=projects).distinct().select_related("role")
    return render(
        request,
        "projects/customer_detail.html",
        {
            "customer": customer,
            "projects": projects,
            "stats": customer.stats(projects),
            "client_people": people.filter(memberships__project__in=projects, memberships__side="client").distinct(),
            "our_people": people.filter(memberships__project__in=projects, memberships__side="internal").distinct(),
            "can_manage": has_cap(request.user, "customer.manage"),
        },
    )


@login_required
def customer_form(request, pk=None):
    require(request.user, "customer.manage")
    customer = get_object_or_404(Customer, pk=pk) if pk else None
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        is_new = customer is None
        obj = form.save(commit=False)
        if is_new:
            obj.created_by = request.user
        obj.save()
        messages.success(request, f"Customer {obj.name} saved.")
        return redirect(obj.get_absolute_url())
    return render(request, "projects/customer_form.html", {"form": form, "customer": customer})


@login_required
@require_POST
def customer_delete(request, pk):
    require(request.user, "customer.manage")
    customer = get_object_or_404(Customer, pk=pk)
    name = customer.name
    customer.delete()
    messages.success(request, f"Customer {name} deleted. Their projects were kept, now unassigned.")
    return redirect("projects:customer_list")


# --------------------------------------------------------------------------- projects
@login_required
def project_list(request):
    qs = visible_projects(request.user).annotate(task_count=Count("tasks", distinct=True))
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    priority = request.GET.get("priority", "")
    show_archived = request.GET.get("archived") == "1"

    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(key__icontains=q) | Q(description__icontains=q))
    if status:
        qs = qs.filter(status=status)
    if priority:
        qs = qs.filter(priority=priority)
    if not show_archived:
        qs = qs.filter(archived=False)
    qs = _filter_by_created(qs, request)
    qs = qs.order_by(Lower("name"))  # alphabetical, A→Z, regardless of the model's default priority ordering

    view_mode = request.GET.get("view", "grid")
    context = {
        "projects": qs,
        "q": q,
        "status": status,
        "priority": priority,
        "show_archived": show_archived,
        "created": request.GET.get("created", ""),
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
        "created_presets": CREATED_PRESETS,
        "view_mode": view_mode,
        "status_choices": Project._meta.get_field("status").choices,
        "priority_choices": Project._meta.get_field("priority").choices,
    }
    context["can_export"] = _is_admin(request.user)
    return render(request, "projects/project_list.html", context)


def _is_admin(user):
    """Superuser, the Administrator role or the Devadmin role."""
    return bool(user.is_authenticated and user.is_site_admin)


@login_required
def project_export(request):
    """Excel of every project (archived ones included, filters ignored), grouped by customer. Admins only."""
    if not _is_admin(request.user):
        raise PermissionDenied("Only admins can download the projects Excel.")
    projects = visible_projects(request.user).select_related("customer", "owner", "team").prefetch_related("vm_links__vm")
    return exports.customers_projects_xlsx(projects)


TAB_CAPS = {"board": "task.view", "list": "task.view", "gantt": "task.view", "plan": "project.plan.view", "activity": "activity.view"}


@login_required
def project_detail(request, pk):
    project = _get_project(request.user, pk)
    tab = request.GET.get("tab", "board")
    if tab == "people":
        return redirect("intake:participants", project.pk)
    require(request.user, TAB_CAPS.get(tab, "task.view"), project)
    tasks = visible_tasks(request.user).filter(project=project).prefetch_related("labels", "children")

    assignee = request.GET.get("assignee", "")
    priority = request.GET.get("priority", "")
    if assignee:
        tasks = tasks.filter(assignee_id=assignee)
    if priority:
        tasks = tasks.filter(priority=priority)

    states = list(project.states)
    columns = [{"state": s, "tasks": [t for t in tasks if t.state_id == s.pk]} for s in states]
    unassigned = [t for t in tasks if t.state_id is None]
    if unassigned and columns:
        columns[0]["tasks"] = unassigned + columns[0]["tasks"]

    gantt_rows = [
        {
            "id": t.pk,
            "title": t.title,
            "start": (t.start_date or t.created_at.date()).isoformat(),
            "end": (t.due_date or t.start_date or t.created_at.date()).isoformat(),
            "progress": t.progress,
            "color": t.state.color if t.state_id else "#94a3b8",
            "assignee": t.assignee.display_name if t.assignee_id else "",
            "url": t.get_absolute_url(),
        }
        for t in tasks
    ]

    context = {
        "project": project,
        "tab": tab,
        "columns": columns,
        "tasks": tasks,
        "roots": [t for t in tasks if t.parent_id is None],
        "milestones": project.milestones.all(),
        "memberships": project.memberships.select_related("user", "role"),
        "activities": project.activities.select_related("actor")[:30],
        "comments": project.comments.select_related("author"),
        "comment_form": CommentForm(),
        "milestone_form": MilestoneForm(),
        "gantt_data": json.dumps(gantt_rows),
        "assignee": assignee,
        "priority": priority,
        "members": User.objects.filter(memberships__project=project),
        "priority_choices": Project._meta.get_field("priority").choices,
        "can_edit": has_cap(request.user, "project.edit", project),
        "can_add_task": has_cap(request.user, "task.create", project),
        "stats": {
            "total": tasks.count(),
            "done": tasks.filter(state__category="done").count(),
            "overdue": tasks.filter(due_date__lt=timezone.localdate()).exclude(state__category="done").count(),
            "estimate": tasks.aggregate(v=Sum("estimate_hours"))["v"] or 0,
            "spent": tasks.aggregate(v=Sum("spent_hours"))["v"] or 0,
        },
    }
    return render(request, "projects/project_detail.html", context)


@login_required
def project_form(request, pk=None):
    if pk:
        project = _get_project(request.user, pk)
        require(request.user, "project.edit", project)
    else:
        project = None
        require(request.user, "project.create")

    form = ProjectForm(request.POST or None, request.FILES or None, instance=project)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if not obj.owner_id:
            obj.owner = request.user
        obj.save()
        form.save_m2m()
        members = form.cleaned_data.get("member_ids")
        if members is not None:
            keep = set()
            for user in members:
                ProjectMembership.objects.get_or_create(
                    project=obj, user=user, defaults={"role": user.role}
                )
                keep.add(user.pk)
            obj.memberships.exclude(user_id__in=keep).delete()
        if obj.owner_id:
            ProjectMembership.objects.get_or_create(
                project=obj, user=obj.owner, defaults={"role": obj.owner.role}
            )
        Activity.log(request.user, f"{'updated' if pk else 'created'} project {obj.key}", project=obj)
        audit_log(request.user, "edit" if pk else "create", "projects", target=obj.key)
        # A Penpot design workspace is queued by apps.penpot.signals on project create.
        if pk:
            messages.success(request, f"Project {obj.key} saved.")
        else:
            # error styling used deliberately here to draw the eye to the VM reminder, not because anything failed
            messages.error(request, f"Project {obj.key} created. Edit it to choose a VM for it.")
        return redirect(obj.get_absolute_url())

    return render(request, "projects/project_form.html", {"form": form, "project": project})


@login_required
def project_po_download(request, pk):
    project = _get_project(request.user, pk)
    if not project.po_document:
        raise Http404("No PO document")
    try:
        handle = project.po_document.open("rb")
    except FileNotFoundError:
        raise Http404("No PO document")
    return FileResponse(handle, as_attachment=True, filename=project.po_filename or os.path.basename(project.po_document.name))


@login_required
@require_POST
def project_delete(request, pk):
    project = _get_project(request.user, pk)
    require(request.user, "project.delete", project)
    key = project.key
    project.delete()
    audit_log(request.user, "delete", "projects", target=key)
    messages.success(request, f"Project {key} deleted.")
    return redirect("projects:list")


@login_required
@require_POST
def project_archive(request, pk):
    project = _get_project(request.user, pk)
    require(request.user, "project.edit", project)
    project.archived = not project.archived
    project.save(update_fields=["archived"])
    Activity.log(request.user, f"{'archived' if project.archived else 'restored'} the project", project=project)
    return redirect(project.get_absolute_url())


# --------------------------------------------------------------------------- tasks
@login_required
def task_detail(request, pk):
    task = _get_task(request.user, pk)
    can_edit = has_cap(request.user, "task.edit", task)
    if request.method == "POST":
        require(request.user, "task.comment", task)
        form = CommentForm(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.task = task
            comment.project = task.project
            comment.author = request.user
            comment.save()
            Activity.log(request.user, "commented on a task", task=task)
            return redirect(task.get_absolute_url())
    context = {
        "task": task,
        "project": task.project,
        "comments": task.comments.select_related("author"),
        "comment_form": CommentForm(),
        "activities": task.activities.select_related("actor")[:20],
        "can_edit": can_edit,
        "states": task.project.states,
    }
    return render(request, "projects/task_detail.html", context)


@login_required
def task_form(request, project_pk=None, pk=None):
    if pk:
        task = _get_task(request.user, pk)
        project = task.project
        require(request.user, "task.edit", task)
    else:
        task = None
        project = _get_project(request.user, project_pk)
        require(request.user, "task.create", project)

    form = TaskForm(request.POST or None, instance=task, project=project)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.project = project
        if not obj.reporter_id:
            obj.reporter = request.user
        obj.save()
        form.save_m2m()
        Activity.log(request.user, f"{'updated' if pk else 'created'} task “{obj.title}”", task=obj)
        messages.success(request, "Task saved.")
        return redirect(obj.get_absolute_url())

    return render(request, "projects/task_form.html", {"form": form, "task": task, "project": project})


@login_required
@require_POST
def task_delete(request, pk):
    task = _get_task(request.user, pk)
    require(request.user, "task.delete", task)
    project = task.project
    task.delete()
    messages.success(request, "Task deleted.")
    return redirect(project.get_absolute_url())


@login_required
def my_tasks(request):
    require(request.user, "mytasks.view")
    tasks = visible_tasks(request.user).filter(assignee=request.user).exclude(state__category="done")
    return render(
        request,
        "projects/my_tasks.html",
        {"tasks": tasks, "done": visible_tasks(request.user).filter(assignee=request.user, state__category="done")[:20]},
    )


# --------------------------------------------------------------------------- JSON endpoints (vanilla fetch)
@login_required
@require_POST
def api_task_move(request):
    """Kanban drag & drop: {task, state, order:[ids]}"""
    payload = json.loads(request.body or "{}")
    task = _get_task(request.user, payload.get("task"))
    if not has_cap(request.user, "task.move", task) and not has_cap(request.user, "task.edit", task):
        raise PermissionDenied("Missing capability: task.move")

    state = get_object_or_404(WorkflowState, pk=payload.get("state"))
    if task.project.workflow_id != state.workflow_id:
        return JsonResponse({"ok": False, "error": "State does not belong to this project's workflow"}, status=400)

    if state.wip_limit and task.state_id != state.pk:
        current = Task.objects.filter(project=task.project, state=state).count()
        if current >= state.wip_limit:
            return JsonResponse({"ok": False, "error": f"WIP limit reached for {state.name}"}, status=400)

    old = task.state.name if task.state_id else "—"
    task.state = state
    task.save(update_fields=["state", "updated_at"])

    for index, task_id in enumerate(payload.get("order") or []):
        Task.objects.filter(pk=task_id, project=task.project).update(order=index)

    Activity.log(request.user, f"moved “{task.title}” from {old} to {state.name}", task=task)
    return JsonResponse({"ok": True, "task": task.pk, "state": state.pk, "progress": task.project.progress})


@login_required
@require_POST
def api_task_quick_create(request):
    payload = json.loads(request.body or "{}")
    project = _get_project(request.user, payload.get("project"))
    require(request.user, "task.create", project)
    title = (payload.get("title") or "").strip()
    if not title:
        return JsonResponse({"ok": False, "error": "Title required"}, status=400)
    state = project.states.filter(pk=payload.get("state")).first() or project.states.first()
    task = Task.objects.create(
        project=project, title=title, state=state, reporter=request.user,
        priority=payload.get("priority") or 3,
    )
    Activity.log(request.user, f"created task “{task.title}”", task=task)
    return JsonResponse(
        {
            "ok": True,
            "task": {
                "id": task.pk,
                "title": task.title,
                "url": task.get_absolute_url(),
                "priority": task.get_priority_display(),
                "state": state.pk if state else None,
            },
        }
    )


@login_required
@require_POST
def api_task_patch(request, pk):
    """Inline edits from the task detail sidebar."""
    task = _get_task(request.user, pk)
    require(request.user, "task.edit", task)
    payload = json.loads(request.body or "{}")
    changed = []
    if "assignee" in payload:
        task.assignee_id = payload["assignee"] or None
        changed.append("assignee")
    if "priority" in payload:
        task.priority = int(payload["priority"])
        changed.append("priority")
    if "state" in payload:
        task.state_id = payload["state"] or None
        changed.append("state")
    if "spent_hours" in payload:
        task.spent_hours = payload["spent_hours"] or 0
        changed.append("spent_hours")
    if changed:
        task.save(update_fields=changed + ["updated_at"])
        Activity.log(request.user, f"updated {', '.join(changed)} on “{task.title}”", task=task)
    return JsonResponse({"ok": True, "changed": changed})


@login_required
def api_project_stats(request, pk):
    project = _get_project(request.user, pk)
    by_state = (
        Task.objects.filter(project=project)
        .values("state__name", "state__color")
        .annotate(n=Count("id"))
        .order_by("state__order")
    )
    by_assignee = (
        Task.objects.filter(project=project)
        .exclude(state__category="done")
        .values("assignee__first_name", "assignee__username")
        .annotate(n=Count("id"))
        .order_by("-n")[:8]
    )
    return JsonResponse(
        {
            "progress": project.progress,
            "by_state": [
                {"label": r["state__name"] or "Unset", "color": r["state__color"] or "#94a3b8", "value": r["n"]}
                for r in by_state
            ],
            "by_assignee": [
                {"label": r["assignee__first_name"] or r["assignee__username"] or "Unassigned", "value": r["n"]}
                for r in by_assignee
            ],
        }
    )


@login_required
@require_POST
def milestone_create(request, pk):
    project = _get_project(request.user, pk)
    require(request.user, "project.edit", project)
    form = MilestoneForm(request.POST)
    if form.is_valid():
        milestone = form.save(commit=False)
        milestone.project = project
        milestone.save()
        Activity.log(request.user, f"added milestone “{milestone.name}”", project=project)
    return redirect(f"{project.get_absolute_url()}?tab=plan")


@login_required
@require_POST
def milestone_toggle(request, pk):
    milestone = get_object_or_404(Milestone, pk=pk)
    project = _get_project(request.user, milestone.project_id)
    require(request.user, "project.edit", project)
    milestone.completed = not milestone.completed
    milestone.save(update_fields=["completed"])
    return redirect(f"{project.get_absolute_url()}?tab=plan")


# --------------------------------------------------------------------------- docs (tech stack, dev instructions, build plan)
def _get_doc_kind(kind):
    if kind not in DOC_KIND_SLUGS:
        raise Http404("Unknown document type")
    return kind


def _plan_sources(project):
    """The three frozen documents a build plan is assembled from."""
    from apps.intake.models import BRD

    tech_doc = ProjectDocument.objects.filter(project=project, kind="tech_stack").first()
    dev_doc = ProjectDocument.objects.filter(project=project, kind="dev_instructions").first()
    brd = BRD.objects.filter(project=project).first()
    sources = [
        {"label": "Tech Stack", "doc": tech_doc, "ready": bool(tech_doc and tech_doc.is_frozen)},
        {"label": "Development Instructions", "doc": dev_doc, "ready": bool(dev_doc and dev_doc.is_frozen)},
        {"label": "BRD", "doc": brd, "ready": bool(brd and brd.is_frozen and brd.current)},
    ]
    return sources, all(s["ready"] for s in sources)


DOC_KIND_CAPS = {"tech_stack": "techstack.view", "dev_instructions": "devinstructions.view", "build_plan": "buildplan.view"}


@login_required
def project_doc_view(request, pk, kind):
    project = _get_project(request.user, pk)
    kind = _get_doc_kind(kind)
    require(request.user, DOC_KIND_CAPS[kind], project)
    doc = ProjectDocument.objects.filter(project=project, kind=kind).first()
    context = {
        "project": project,
        "doc": doc,
        "kind": kind,
        "kind_label": dict(DOC_KINDS)[kind],
        "html": render_markdown(doc.content) if doc else "",
        "can_edit": has_cap(request.user, "project.edit", project),
        "tab": kind,
    }
    if kind == "build_plan":
        sources, sources_ready = _plan_sources(project)
        context["plan_sources"] = sources
        context["plan_sources_ready"] = sources_ready
    return render(request, "projects/project_doc.html", context)


@login_required
def project_doc_edit(request, pk, kind):
    project = _get_project(request.user, pk)
    kind = _get_doc_kind(kind)
    require(request.user, "project.edit", project)
    doc, _created = ProjectDocument.objects.get_or_create(
        project=project, kind=kind, defaults={"content": DOC_DEFAULT_CONTENT.get(kind, "")}
    )
    if doc.is_frozen:
        messages.error(request, f"{dict(DOC_KINDS)[kind]} is frozen. Unfreeze it first to make further edits.")
        return redirect("projects:doc_view", project.pk, kind)
    form = ProjectDocumentForm(request.POST or None, instance=doc)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.updated_by = request.user
        obj.save()
        Activity.log(request.user, f"updated the {dict(DOC_KINDS)[kind]}", project=project)
        messages.success(request, f"{dict(DOC_KINDS)[kind]} saved.")
        return redirect("projects:doc_view", project.pk, kind)
    return render(
        request,
        "projects/project_doc_edit.html",
        {"project": project, "doc": doc, "kind": kind, "kind_label": dict(DOC_KINDS)[kind], "form": form},
    )


@login_required
@require_POST
def project_doc_freeze(request, pk, kind):
    project = _get_project(request.user, pk)
    kind = _get_doc_kind(kind)
    require(request.user, "project.edit", project)
    doc, _created = ProjectDocument.objects.get_or_create(project=project, kind=kind)
    label = dict(DOC_KINDS)[kind]
    if doc.is_frozen:
        doc.is_frozen, doc.frozen_at, doc.frozen_by = False, None, None
        doc.save(update_fields=["is_frozen", "frozen_at", "frozen_by"])
        Activity.log(request.user, f"unfroze the {label}", project=project)
        messages.success(request, f"{label} unfrozen — it can be edited again.")
    else:
        doc.is_frozen, doc.frozen_at, doc.frozen_by = True, timezone.now(), request.user
        doc.save(update_fields=["is_frozen", "frozen_at", "frozen_by"])
        Activity.log(request.user, f"froze the {label} as final", project=project)
        messages.success(request, f"{label} frozen as final.")
    return redirect("projects:doc_view", project.pk, kind)


@login_required
@require_POST
def project_doc_generate_plan(request, pk):
    project = _get_project(request.user, pk)
    require(request.user, "project.edit", project)
    sources, sources_ready = _plan_sources(project)
    if not sources_ready:
        missing = ", ".join(s["label"] for s in sources if not s["ready"])
        messages.error(request, f"Freeze these first before generating the build plan: {missing}.")
        return redirect("projects:doc_view", project.pk, "build_plan")

    tech_doc = next(s["doc"] for s in sources if s["label"] == "Tech Stack")
    dev_doc = next(s["doc"] for s in sources if s["label"] == "Development Instructions")
    brd = next(s["doc"] for s in sources if s["label"] == "BRD")

    doc, _created = ProjectDocument.objects.get_or_create(project=project, kind="build_plan")
    if doc.is_frozen:
        messages.error(request, "Build Plan is frozen. Unfreeze it first to regenerate.")
        return redirect("projects:doc_view", project.pk, "build_plan")

    from apps.jobs.handlers import queue_build_plan
    from apps.jobs.services import QueueFull, queued_message

    try:
        job, created = queue_build_plan(project, request.user)
        messages.success(request, queued_message(job, created))
    except QueueFull as exc:
        messages.error(request, str(exc))
    return redirect("projects:doc_view", project.pk, "build_plan")


@login_required
def project_doc_download(request, pk, kind):
    project = _get_project(request.user, pk)
    kind = _get_doc_kind(kind)
    doc = get_object_or_404(ProjectDocument, project=project, kind=kind)
    response = HttpResponse(doc.content, content_type="text/markdown; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{doc.filename}"'
    return response
