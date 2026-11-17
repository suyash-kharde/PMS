"""Queryset scoping + the milestone rollup for the Claude Code dev log."""

from django.db.models import Q

from apps.accounts.models import SCOPE_ALL, SCOPE_TEAM
from apps.accounts.permissions import scope_of
from apps.projects.selectors import visible_projects

from .models import DevUpdate


def visible_dev_updates(user):
    """Dev updates on projects the user can see — gated by the `devlog.view` capability,
    mirroring apps.sprints.selectors._project_scoped."""
    qs = DevUpdate.objects.select_related(
        "project", "created_by", "sprint", "requirement", "ticket", "task"
    )
    if not user.is_authenticated:
        return qs.none()
    scope = scope_of(user, "devlog.view")
    if user.is_superuser or scope == SCOPE_ALL:
        return qs
    if scope is None:
        return qs.filter(created_by=user)

    reach = Q(project__in=visible_projects(user)) | Q(created_by=user)
    if scope == SCOPE_TEAM:
        reach |= Q(project__owner_id__in=user.subordinate_ids())
    return qs.filter(reach).distinct()


def milestone_progress(updates_qs):
    """-> {"milestones": [ {label, done, status, count, last_at} ], "done": N, "total": M}.

    Milestones are the distinct non-empty `milestone` labels, in the order they were first
    seen (oldest update first). A milestone counts as done once any update for it is flagged
    `milestone_done`; its shown status is that of the most recent update naming it.
    """
    rows = list(updates_qs.order_by("created_at").values("milestone", "milestone_done", "status", "created_at"))
    order = []
    by_label = {}
    for r in rows:
        label = (r["milestone"] or "").strip()
        if not label:
            continue
        if label not in by_label:
            order.append(label)
            by_label[label] = {"label": label, "done": False, "status": r["status"], "count": 0, "last_at": None}
        entry = by_label[label]
        entry["count"] += 1
        entry["status"] = r["status"]  # rows are time-ordered, so the last write wins
        entry["last_at"] = r["created_at"]
        if r["milestone_done"]:
            entry["done"] = True

    milestones = [by_label[label] for label in order]
    done = sum(1 for m in milestones if m["done"])
    return {"milestones": milestones, "done": done, "total": len(milestones)}
