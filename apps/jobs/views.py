from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import require

from . import services
from .models import ACTIVE, Job


def is_admin(user):
    return bool(user.is_site_admin)


def visible_jobs(user):
    if is_admin(user):
        return Job.objects.all()
    from django.db.models import Q

    from apps.projects.selectors import visible_projects

    return Job.objects.filter(Q(requested_by=user) | Q(project__in=visible_projects(user)))


def can_manage(user, job):
    return is_admin(user) or job.requested_by_id == user.pk


@login_required
def queue_page(request):
    require(request.user, "aiqueue.view")
    jobs = visible_jobs(request.user).select_related("project", "requested_by")
    active = list(jobs.filter(status__in=ACTIVE).order_by("created_at"))
    recent = list(jobs.exclude(status__in=ACTIVE)
                  .filter(finished_at__gte=timezone.now() - timezone.timedelta(days=2)).order_by("-finished_at")[:50])
    rows = [{"job": j, "info": services.describe(j), "can_manage": can_manage(request.user, j)} for j in active + recent]
    return render(request, "jobs/queue.html", {
        "running": [r for r in rows if r["job"].status == "running"],
        "waiting": [r for r in rows if r["job"].status == "queued"],
        "recent": [r for r in rows if not r["job"].is_active],
        "lanes": services.lanes(), "is_admin": is_admin(request.user),
    })


@login_required
def api_job(request, pk):
    job = get_object_or_404(visible_jobs(request.user), pk=pk)
    return JsonResponse(services.describe(job))


@login_required
@require_POST
def job_cancel(request, pk):
    job = get_object_or_404(visible_jobs(request.user), pk=pk)
    if not can_manage(request.user, job):
        raise PermissionDenied("Only the person who asked for it, or an admin, can cancel this.")
    if services.cancel(job):
        messages.success(request, f"Removed from the queue: {job.label}.")
    else:
        messages.error(request, "Only a waiting job can be cancelled; a running one will finish.")
    return redirect(request.POST.get("next") or "jobs:queue")


@login_required
@require_POST
def job_retry(request, pk):
    job = get_object_or_404(visible_jobs(request.user), pk=pk)
    if not can_manage(request.user, job):
        raise PermissionDenied("Only the person who asked for it, or an admin, can retry this.")
    if services.retry(job):
        messages.success(request, services.queued_message(job, True))
    else:
        messages.error(request, "Only a failed or cancelled job can be retried.")
    return redirect(request.POST.get("next") or "jobs:queue")
