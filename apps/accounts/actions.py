"""Actions a person took (not pages they only opened), counted per kind, for "Actions today" on
People and the person's "Work on <day>". Read from the activity records the apps already keep:

  * project activity feed (projects.Activity): tasks, BRD/timeline, sprints, dev log, project docs
  * ticket events (tickets.TicketEvent): raising, comments, status moves, attachments, assigning
  * timesheet changes (timesheet.TimesheetAudit): one per day of hours saved or submitted

Automatic work (timesheet auto-fill, page visits, background jobs) is never counted.
"""
from collections import Counter, OrderedDict

KINDS = ["Tasks", "Tickets", "BRD & timeline", "Sprints & requirements", "Timesheet", "Dev log", "Projects & docs"]

_KEYWORDS = [  # first match wins; checked against the lower-cased activity text
    ("Tasks", ("task",)),
    ("BRD & timeline", ("brd", "timeline", "transcript")),
    ("Sprints & requirements", ("sprint", "requirement", "criterion", "test case", "field spec", "risk",
                                "dependenc", "service")),
    ("Dev log", ("dev update",)),
]


def activity_kind(activity):
    if activity.task_id:
        return "Tasks"
    verb = activity.verb.lower()
    for kind, words in _KEYWORDS:
        if any(w in verb for w in words):
            return kind
    return "Projects & docs"


def action_counts(user_ids, start, end):
    """-> {user_id: {"total": n, "kinds": OrderedDict(kind -> n)}} for actions in [start, end)."""
    from apps.projects.models import Activity
    from apps.tickets.models import TicketEvent
    from apps.timesheet.models import TimesheetAudit

    counts = {uid: Counter() for uid in user_ids}
    for a in Activity.objects.filter(actor_id__in=user_ids, created_at__gte=start, created_at__lt=end).only(
            "actor_id", "task_id", "verb"):
        counts[a.actor_id][activity_kind(a)] += 1
    for uid, n in Counter(TicketEvent.objects.filter(actor_id__in=user_ids, created_at__gte=start, created_at__lt=end)
                          .values_list("actor_id", flat=True)).items():
        counts[uid]["Tickets"] += n
    # one save writes a row per changed cell: count each (person, timesheet date) once
    days = {(uid, ref[:10]) for uid, ref in TimesheetAudit.objects.filter(
        actor_id__in=user_ids, entity__in=("hours", "day_status"), created_at__gte=start, created_at__lt=end,
    ).values_list("actor_id", "ref")}
    for uid, _day in days:
        counts[uid]["Timesheet"] += 1
    return {
        uid: {"total": sum(c.values()), "kinds": OrderedDict((k, c[k]) for k in KINDS if c[k])}
        for uid, c in counts.items()
    }
