from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from apps.projects.models import PRIORITIES

# --------------------------------------------------------------------------- choices
SPRINT_STATUS = [
    ("planning", "Planning"),
    ("active", "Active"),
    ("completed", "Completed"),
    ("cancelled", "Cancelled"),
]

# A requirement's own lifecycle: written -> ready for a sprint -> being built ->
# in review -> done. `deferred` parks it out of the current plan.
REQUIREMENT_STATUS = [
    ("draft", "Draft"),
    ("ready", "Ready"),
    ("in_progress", "In progress"),
    ("in_review", "In review"),
    ("done", "Done"),
    ("deferred", "Deferred"),
]
REQ_OPEN_STATUSES = {"draft", "ready", "in_progress", "in_review"}
REQ_DONE_STATUSES = {"done", "deferred"}

# section 8 — a criterion is either a plain statement ("User can enter first name.")
# or a structured Given/When/Then triple suitable for automated testing.
CRITERION_FORMATS = [
    ("statement", "Statement"),
    ("gwt", "Given / When / Then"),
]

# section 9 — field-level specification
FIELD_TYPES = [
    ("text", "Text"),
    ("textarea", "Long text"),
    ("email", "Email"),
    ("number", "Number"),
    ("phone", "Phone"),
    ("date", "Date"),
    ("datetime", "Date & time"),
    ("boolean", "Yes / No"),
    ("select", "Choice"),
    ("file", "File"),
]
MANDATORY_CHOICES = [
    ("yes", "Yes"),
    ("no", "No"),
    ("depends", "Depends"),
]

# section 10 — unit test case types
TEST_TYPES = [
    ("positive", "Positive"),
    ("negative", "Negative"),
    ("validation", "Validation"),
    ("boundary", "Boundary"),
    ("integration", "Integration"),
    ("error_handling", "Error handling"),
]
TEST_STATUS = [
    ("not_run", "Not run"),
    ("pass", "Pass"),
    ("fail", "Fail"),
    ("blocked", "Blocked"),
]

# section 11 — requirement-level risks
RISK_CATEGORIES = [
    ("third_party_integration", "Third-party integration"),
    ("client_dependency", "Client dependency"),
    ("technical", "Technical"),
    ("resourcing", "Resourcing"),
    ("schedule", "Schedule"),
    ("security", "Security"),
    ("data", "Data"),
    ("other", "Other"),
]
RISK_STATUS = [
    ("open", "Open"),
    ("mitigating", "Mitigating"),
    ("accepted", "Accepted"),
    ("closed", "Closed"),
]
LIKELIHOOD_IMPACT = [
    (1, "Very low"),
    (2, "Low"),
    (3, "Medium"),
    (4, "High"),
    (5, "Very high"),
]

# section 12 — dependency management
DEPENDENCY_KINDS = [
    ("internal", "Internal"),
    ("external", "External"),
]
DEPENDENCY_STATUS = [
    ("pending", "Pending"),
    ("in_progress", "In progress"),
    ("resolved", "Resolved"),
    ("blocked", "Blocked"),
]

# section 13 — serviceable / external services
SERVICE_CATEGORIES = [
    ("stripe", "Stripe account"),
    ("payment_gateway", "Payment gateway account"),
    ("subscription_provider", "Subscription provider"),
    ("smtp", "SMTP credentials"),
    ("email_service", "Email service"),
    ("sms_gateway", "SMS gateway"),
    ("cloud_account", "Cloud account"),
    ("domain", "Domain"),
    ("api_credentials", "API credentials"),
    ("software_license", "Third-party software license"),
    ("external_database", "External database"),
    ("client_data", "Client-provided data"),
    ("hosting", "Hosting information"),
    ("server_access", "Server access"),
    ("other", "Other"),
]
SERVICE_PROVIDERS = [
    ("client", "Client"),
    ("internal", "Our organisation"),
    ("third_party", "Third party"),
]
SERVICE_STATUS = [
    ("identified", "Identified"),
    ("requested", "Requested"),
    ("received", "Received"),
    ("configured", "Configured"),
    ("verified", "Verified"),
    ("blocked", "Blocked"),
]


def _next_number(model, project_id):
    last = model.objects.filter(project_id=project_id).aggregate(n=Max("number"))["n"]
    return (last or 0) + 1


# --------------------------------------------------------------------------- Sprint
class Sprint(models.Model):
    """A time-boxed plan for a project: a goal plus the requirements committed to it."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="sprints")
    number = models.PositiveIntegerField(editable=False, help_text="Per-project sequence, e.g. PLAT-S1")

    name = models.CharField(max_length=160)
    goal = models.TextField(blank=True, help_text="What this sprint sets out to achieve")
    status = models.CharField(max_length=12, choices=SPRINT_STATUS, default="planning")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    capacity_points = models.PositiveIntegerField(default=0, help_text="0 = not set")

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_sprints"
    )
    generated_by = models.ForeignKey(
        "SprintPlanRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "-number"]
        unique_together = ("project", "number")
        indexes = [models.Index(fields=["project", "status"])]

    def __str__(self):
        return f"{self.key} · {self.name}"

    def get_absolute_url(self):
        return reverse("sprints:sprint_detail", args=[self.pk])

    def save(self, *args, **kwargs):
        if not self.number and self.project_id:
            self.number = _next_number(Sprint, self.project_id)
        super().save(*args, **kwargs)

    @property
    def key(self):
        return f"{self.project.key}-S{self.number}"

    @property
    def is_active(self):
        return self.status == "active"

    @property
    def requirement_count(self):
        return self.requirements.count()

    @property
    def points_committed(self):
        return self.requirements.aggregate(v=models.Sum("estimate_points"))["v"] or 0

    @property
    def progress(self):
        total = self.requirements.count()
        if not total:
            return 0
        done = self.requirements.filter(status="done").count()
        return round(done * 100 / total)


# --------------------------------------------------------------------------- Requirement
class Requirement(models.Model):
    """A single requirement. Lives in the project backlog until committed to a sprint."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="requirements")
    sprint = models.ForeignKey(
        Sprint, null=True, blank=True, on_delete=models.SET_NULL, related_name="requirements",
        help_text="Leave empty to keep it in the backlog",
    )
    number = models.PositiveIntegerField(editable=False, help_text="Per-project sequence, e.g. PLAT-R1")

    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    user_story = models.TextField(blank=True, help_text="As a <role> I want <goal> so that <benefit>")
    user_story_id = models.CharField(max_length=40, blank=True, help_text="External story reference, e.g. JIRA-123")

    priority = models.PositiveSmallIntegerField(choices=PRIORITIES, default=3)
    status = models.CharField(max_length=12, choices=REQUIREMENT_STATUS, default="draft")
    estimate_points = models.PositiveIntegerField(default=0, help_text="0 = not estimated")

    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_requirements"
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reported_requirements"
    )
    linked_task = models.ForeignKey(
        "projects.Task", null=True, blank=True, on_delete=models.SET_NULL, related_name="requirements",
        help_text="Delivery task built from this requirement",
    )
    generated_by = models.ForeignKey(
        "SprintPlanRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "-created_at"]
        unique_together = ("project", "number")
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["sprint"]),
            models.Index(fields=["assignee"]),
        ]

    def __str__(self):
        return f"{self.key} · {self.title}"

    def get_absolute_url(self):
        return reverse("sprints:requirement_detail", args=[self.pk])

    def save(self, *args, **kwargs):
        if not self.number and self.project_id:
            self.number = _next_number(Requirement, self.project_id)
        super().save(*args, **kwargs)

    @property
    def key(self):
        return f"{self.project.key}-R{self.number}"

    @property
    def is_open(self):
        return self.status in REQ_OPEN_STATUSES

    @property
    def criteria_met(self):
        rows = list(self.criteria.all())
        return sum(1 for c in rows if c.is_met), len(rows)

    @property
    def test_summary(self):
        rows = list(self.test_cases.all())
        return {
            "total": len(rows),
            "passed": sum(1 for t in rows if t.status == "pass"),
            "failed": sum(1 for t in rows if t.status == "fail"),
        }


# --------------------------------------------------------------------------- AcceptanceCriterion
class AcceptanceCriterion(models.Model):
    """Defines exactly when a requirement is complete — a statement or a G/W/T triple."""

    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name="criteria")
    order = models.PositiveIntegerField(default=0)
    format = models.CharField(max_length=10, choices=CRITERION_FORMATS, default="statement")

    statement = models.TextField(blank=True, help_text='e.g. "User can enter first name."')
    given_text = models.TextField(blank=True, help_text="Given …")
    when_text = models.TextField(blank=True, help_text="When …")
    then_text = models.TextField(blank=True, help_text="Then …")

    is_met = models.BooleanField(default=False, help_text="Verified as satisfied")
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text[:80]

    def clean(self):
        if self.format == "statement":
            if not self.statement.strip():
                raise ValidationError({"statement": "A statement is required."})
        elif not (self.given_text.strip() and self.when_text.strip() and self.then_text.strip()):
            raise ValidationError("Given, When and Then are all required for a structured criterion.")

    @property
    def text(self):
        if self.format == "gwt":
            return f"Given {self.given_text.strip()} When {self.when_text.strip()} Then {self.then_text.strip()}"
        return self.statement.strip()


# --------------------------------------------------------------------------- FieldSpec
class FieldSpec(models.Model):
    """section 9 — the expected fields and their behaviour for a requirement's form/screen."""

    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name="field_specs")
    order = models.PositiveIntegerField(default=0)

    name = models.CharField(max_length=120, help_text='e.g. "First Name"')
    field_type = models.CharField(max_length=12, choices=FIELD_TYPES, default="text")
    mandatory = models.CharField(max_length=8, choices=MANDATORY_CHOICES, default="no")
    mandatory_condition = models.CharField(
        max_length=200, blank=True, help_text='When mandatory is "Depends" — the condition'
    )
    max_length = models.PositiveIntegerField(null=True, blank=True, help_text="Configured length")
    validation = models.CharField(
        max_length=200, blank=True, help_text='e.g. "Alphabetic / configured length", "Valid email format"'
    )
    default_value = models.CharField(max_length=200, blank=True)
    notes = models.CharField(max_length=240, blank=True)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.name} ({self.get_field_type_display()})"


# --------------------------------------------------------------------------- RequirementRisk
class RequirementRisk(models.Model):
    """section 11 — a risk associated with a requirement."""

    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name="risks")
    category = models.CharField(max_length=32, choices=RISK_CATEGORIES, default="other")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    likelihood = models.PositiveSmallIntegerField(choices=LIKELIHOOD_IMPACT, default=3)
    impact = models.PositiveSmallIntegerField(choices=LIKELIHOOD_IMPACT, default=3)
    mitigation = models.TextField(blank=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_req_risks"
    )
    owner_note = models.CharField(max_length=120, blank=True, help_text="External owner, if not a system user")
    status = models.CharField(max_length=12, choices=RISK_STATUS, default="open")
    target_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def score(self):
        return self.likelihood * self.impact

    @property
    def severity(self):
        s = self.score
        if s >= 15:
            return "high"
        if s >= 8:
            return "medium"
        return "low"


# --------------------------------------------------------------------------- Dependency
class Dependency(models.Model):
    """section 12 — an internal or external dependency of a requirement."""

    requirement = models.ForeignKey(Requirement, on_delete=models.CASCADE, related_name="dependencies")
    kind = models.CharField(max_length=10, choices=DEPENDENCY_KINDS, default="internal")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    depends_on = models.ForeignKey(
        Requirement, null=True, blank=True, on_delete=models.SET_NULL, related_name="dependents",
        help_text="Internal: the requirement that must be completed first",
    )
    external_type = models.CharField(
        max_length=120, blank=True,
        help_text='External: e.g. "Client credentials", "Stripe account", "SMTP credentials"',
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_dependencies"
    )
    owner_note = models.CharField(max_length=120, blank=True, help_text="External owner, if not a system user")
    due_date = models.DateField(null=True, blank=True, help_text="Expected completion date")
    status = models.CharField(max_length=12, choices=DEPENDENCY_STATUS, default="pending")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "due_date", "id"]
        verbose_name_plural = "dependencies"

    def __str__(self):
        return f"[{self.get_kind_display()}] {self.title}"

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < timezone.localdate() and self.status != "resolved")


# --------------------------------------------------------------------------- UnitTestCase
class UnitTestCase(models.Model):
    """section 10 — a developer unit test derived from a requirement / acceptance criterion."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="test_cases")
    requirement = models.ForeignKey(
        Requirement, null=True, blank=True, on_delete=models.SET_NULL, related_name="test_cases"
    )
    criterion = models.ForeignKey(
        AcceptanceCriterion, null=True, blank=True, on_delete=models.SET_NULL, related_name="test_cases"
    )
    number = models.PositiveIntegerField(editable=False, help_text="Per-project sequence, e.g. PLAT-TC1")

    title = models.CharField(max_length=240)
    test_type = models.CharField(max_length=16, choices=TEST_TYPES, default="positive")

    preconditions = models.TextField(blank=True)
    input_data = models.TextField(blank=True)
    steps = models.TextField(blank=True, help_text="One step per line")
    expected_result = models.TextField(blank=True)
    actual_result = models.TextField(blank=True)

    status = models.CharField(max_length=10, choices=TEST_STATUS, default="not_run")
    developer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="unit_tests"
    )
    executed_on = models.DateField(null=True, blank=True)
    comments = models.TextField(blank=True)
    user_story_id = models.CharField(max_length=40, blank=True, help_text="Overrides the requirement's story id")
    generated_by = models.ForeignKey(
        "SprintPlanRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("project", "number")
        indexes = [
            models.Index(fields=["project", "status"]),
            models.Index(fields=["requirement"]),
        ]

    def __str__(self):
        return f"{self.key} · {self.title}"

    def get_absolute_url(self):
        return reverse("sprints:test_case_detail", args=[self.pk])

    def save(self, *args, **kwargs):
        if not self.number and self.project_id:
            self.number = _next_number(UnitTestCase, self.project_id)
        super().save(*args, **kwargs)

    @property
    def key(self):
        return f"{self.project.key}-TC{self.number}"

    @property
    def story_id(self):
        return self.user_story_id or (self.requirement.user_story_id if self.requirement_id else "")

    @property
    def requirement_key(self):
        return self.requirement.key if self.requirement_id else "—"


# --------------------------------------------------------------------------- ExternalService
class ExternalService(models.Model):
    """section 13 — a project-specific service required from the client or a third party."""

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="external_services")
    category = models.CharField(max_length=24, choices=SERVICE_CATEGORIES, default="other")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    provided_by = models.CharField(max_length=12, choices=SERVICE_PROVIDERS, default="client")
    status = models.CharField(max_length=12, choices=SERVICE_STATUS, default="identified")

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_services"
    )
    owner_note = models.CharField(max_length=120, blank=True, help_text="External owner, if not a system user")
    expected_date = models.DateField(null=True, blank=True)
    received_date = models.DateField(null=True, blank=True)
    details = models.TextField(
        blank=True, help_text="Where the config/credentials live — never store the secrets themselves here"
    )
    requirements = models.ManyToManyField(
        Requirement, blank=True, related_name="external_services", help_text="Requirements that need this service"
    )
    generated_by = models.ForeignKey(
        "SprintPlanRun", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "category", "name"]

    def __str__(self):
        return f"{self.project.key} · {self.name}"

    def get_absolute_url(self):
        return reverse("sprints:service_list", args=[self.project_id])

    @property
    def is_ready(self):
        return self.status in ("configured", "verified")

    @property
    def is_overdue(self):
        return bool(
            self.expected_date
            and self.expected_date < timezone.localdate()
            and not self.is_ready
        )


# --------------------------------------------------------------------------- AI generation
PLAN_RUN_STATUS = [
    ("queued", "Queued"),
    ("running", "Running"),
    ("done", "Done"),
    ("failed", "Failed"),
    ("cancelled", "Cancelled"),
]
PLAN_RUN_TERMINAL = {"done", "failed", "cancelled"}

PLAN_STEP_STATUS = [
    ("pending", "Pending"),
    ("running", "Running"),
    ("done", "Done"),
    ("failed", "Failed"),
    ("skipped", "Skipped"),
]


class SprintPlanRun(models.Model):
    """One AI generation pass: reads the project's documents and populates the sprint module.

    Executed by the background worker (`process_queue`); the run page polls its status.
    """

    project = models.ForeignKey("projects.Project", on_delete=models.CASCADE, related_name="plan_runs")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="sprint_plan_runs"
    )
    status = models.CharField(max_length=12, choices=PLAN_RUN_STATUS, default="queued")
    phase = models.CharField(max_length=120, blank=True, help_text="Current phase, shown live")

    options = models.JSONField(default=dict, blank=True)
    sources = models.JSONField(default=dict, blank=True)
    research = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True, help_text="Final counts")

    summary = models.TextField(blank=True)
    error = models.TextField(blank=True)
    log = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Plan run #{self.pk} · {self.project.key} · {self.status}"

    def get_absolute_url(self):
        return reverse("sprints:plan_run_detail", args=[self.pk])

    @property
    def is_terminal(self):
        return self.status in PLAN_RUN_TERMINAL

    @property
    def duration(self):
        if not self.started_at:
            return None
        end = self.finished_at or timezone.now()
        return round((end - self.started_at).total_seconds())

    def append_log(self, message):
        stamp = timezone.now().strftime("%H:%M:%S")
        self.log = f"{self.log}\n[{stamp}] {message}".strip()
        self.save(update_fields=["log"])


class SprintPlanStep(models.Model):
    """One visible step in a run — pre-created so the UI shows the whole checklist up front."""

    run = models.ForeignKey(SprintPlanRun, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveIntegerField(default=0)
    key = models.CharField(max_length=80, help_text="e.g. 'research', 'elaborate:PLAT-R7'")
    label = models.CharField(max_length=160)
    status = models.CharField(max_length=10, choices=PLAN_STEP_STATUS, default="pending")
    detail = models.CharField(max_length=300, blank=True, help_text="One-line result")
    log = models.TextField(blank=True)
    output = models.JSONField(null=True, blank=True)
    model_name = models.CharField(max_length=120, blank=True)
    tokens = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order", "id"]
        unique_together = ("run", "key")

    def __str__(self):
        return f"{self.run_id}:{self.key} ({self.status})"

    def start(self):
        self.status = "running"
        self.started_at = timezone.now()
        self.save(update_fields=["status", "started_at"])

    def finish(self, status="done", detail="", output=None, model_name="", tokens=0):
        self.status = status
        self.detail = detail[:300]
        if output is not None:
            self.output = output
        if model_name:
            self.model_name = model_name
        if tokens:
            self.tokens = tokens
        self.finished_at = timezone.now()
        self.save()
