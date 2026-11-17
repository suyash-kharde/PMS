"""Timesheet tables. Everything here is new and self-contained: other apps are only read
(projects, memberships, managers, logins), never changed."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class Holiday(models.Model):
    """A day off. `teams` empty = everyone; otherwise only people in those teams (and their sub-teams,
    and teams that follow them — see TeamHolidayLink). Two holidays may share a date for different teams."""

    date = models.DateField()
    name = models.CharField(max_length=120)
    teams = models.ManyToManyField("accounts.Team", blank=True, related_name="+")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "pk"]
        indexes = [models.Index(fields=["date"], name="ts_holiday_date_idx")]

    def __str__(self):
        return f"{self.date:%d %b %Y} · {self.label}"

    @property
    def applies_to(self):
        names = [t.name for t in self.teams.all()]
        return ", ".join(sorted(names, key=str.lower)) if names else "Everyone"

    @property
    def label(self):
        """Name plus the teams it is limited to, e.g. 'Ganesh Chaturthi (pune team)'."""
        applies = self.applies_to
        return self.name if applies == "Everyone" else f"{self.name} ({applies})"


class WorkingDay(models.Model):
    """A Saturday or Sunday (normally off) that is a normal working day for everyone: 8 hours auto-filled,
    attendance tracked. A holiday on the same date still wins. The opposite case, a normally-working day that
    is off, is just a Holiday."""

    date = models.DateField()
    name = models.CharField(max_length=120)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "pk"]
        indexes = [models.Index(fields=["date"], name="ts_workingday_date_idx")]

    def __str__(self):
        return f"{self.date:%d %b %Y} · {self.name}"


class TeamHolidayLink(models.Model):
    """A team that takes another team's holidays, e.g. the UI/UX team sits in Pune, so it
    follows Pune's holidays. Timesheet-only: the org chart and team access are not affected."""

    team = models.OneToOneField("accounts.Team", on_delete=models.CASCADE, related_name="+")
    follows = models.ForeignKey("accounts.Team", on_delete=models.CASCADE, related_name="+")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.team} follows {self.follows}"


class TimesheetProfile(models.Model):
    """Per-person timesheet settings. Created on first use; joining date defaults to the account's."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="timesheet_profile")
    joining_date = models.DateField(null=True, blank=True, help_text="No hours before this date")
    last_working_day = models.DateField(null=True, blank=True, help_text="No hours after this date")
    remainder_project = models.ForeignKey(
        "projects.Project", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Gets the leftover hours of the 8-hour split; blank = the first eligible project",
    )

    def __str__(self):
        return f"Timesheet profile · {self.user}"


class TimesheetMainProject(models.Model):
    """A project the person marked as one of their main projects (the star on the week view).
    When someone has any, the daily 8 hours are split across those only; other projects start at 0.
    At most rules.DAILY_HOURS open ones, so every main project gets at least 1 whole hour."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="timesheet_main_projects")
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="+")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "project"], name="ts_main_user_project")]

    def __str__(self):
        return f"{self.user} ★ {self.project_id}"


ATTENDANCE_STATUS = [("present", "Present"), ("absent", "Absent")]


class Attendance(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField()
    status = models.CharField(max_length=10, choices=ATTENDANCE_STATUS)
    first_login = models.DateTimeField(null=True, blank=True)
    last_logout = models.DateTimeField(null=True, blank=True)
    overridden = models.BooleanField(default=False)
    override_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    override_reason = models.CharField(max_length=240, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]
        constraints = [models.UniqueConstraint(fields=["user", "date"], name="ts_attendance_user_date")]

    def __str__(self):
        return f"{self.user} {self.date}: {self.status}"


DAY_STATUS = [("draft", "Draft"), ("submitted", "Submitted")]


class TimesheetDay(models.Model):
    """One person's timesheet for one date. `edited` = a person changed it, so auto-fill
    never overwrites it again. `leave_hours` = the Leave row (8 = a full day of leave)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="timesheet_days")
    date = models.DateField()
    status = models.CharField(max_length=10, choices=DAY_STATUS, default="draft")
    auto_filled = models.BooleanField(default=False)
    edited = models.BooleanField(default=False)
    leave_hours = models.PositiveSmallIntegerField(
        default=0, help_text="Hours on leave that day (the Leave row). They count toward the 8-hour day."
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["user", "date"], name="ts_day_user_date")]
        indexes = [models.Index(fields=["date"], name="ts_day_date_idx")]

    def __str__(self):
        return f"{self.user} {self.date} ({self.status})"

    @property
    def total(self):
        return sum(e.hours for e in self.entries.all())


class TimesheetEntry(models.Model):
    day = models.ForeignKey(TimesheetDay, on_delete=models.CASCADE, related_name="entries")
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="timesheet_entries")
    hours = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["day", "project"], name="ts_entry_day_project")]

    def __str__(self):
        return f"{self.day} · {self.project_id}: {self.hours}h"


class ProjectStatusLog(models.Model):
    """Every project status change, so a past date knows whether the project was Completed then
    (hours stop after completion and resume from a reactivation)."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="+")
    old_status = models.CharField(max_length=12, blank=True)
    new_status = models.CharField(max_length=12)
    changed_at = models.DateTimeField(default=timezone.now)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["changed_at", "pk"]
        indexes = [models.Index(fields=["project", "changed_at"], name="ts_pstatus_idx")]


class TimesheetUnlock(models.Model):
    """Admin reopened a locked month for one person so they can edit it themselves."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="timesheet_unlocks")
    month = models.DateField(help_text="First day of the unlocked month")
    active = models.BooleanField(default=True)
    unlocked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


AUDIT_ENTITIES = [
    ("hours", "Hours"),
    ("day_status", "Draft / submit"),
    ("attendance", "Attendance"),
    ("holiday", "Holiday"),
    ("project_status", "Project status"),
    ("unlock", "Unlock"),
    ("lock", "Month lock"),
    ("settings", "Timesheet settings"),
]


class TimesheetAudit(models.Model):
    """Who changed what and when, with old and new values."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    entity = models.CharField(max_length=20, choices=AUDIT_ENTITIES)
    ref = models.CharField(max_length=200, blank=True, help_text="What was changed, e.g. '2026-09-28 · PLAT'")
    old_value = models.CharField(max_length=255, blank=True)
    new_value = models.CharField(max_length=255, blank=True)
    after_lock = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["created_at"], name="ts_audit_time_idx")]


class JobRun(models.Model):
    """Marks a scheduled job as done (e.g. 'weekly:2026-09-28') so it runs exactly once."""

    key = models.CharField(max_length=60, unique=True)
    ran_at = models.DateTimeField(default=timezone.now)
    detail = models.CharField(max_length=255, blank=True)


class PastAssignment(models.Model):
    """A project a person was on and has been removed from, with the dates. Lets them keep editing
    (in full) that project's hours for the days they were on it, while the month is open."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="past_assignments")
    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="+")
    started_on = models.DateField()
    ended_on = models.DateField()
    removed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-ended_on", "-pk"]
        indexes = [models.Index(fields=["user", "ended_on"], name="ts_pastassign_idx")]

    def __str__(self):
        return f"{self.user} on {self.project_id}: {self.started_on} → {self.ended_on}"

