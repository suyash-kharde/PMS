from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

PRIORITIES = [
    (1, "Critical"),
    (2, "High"),
    (3, "Medium"),
    (4, "Low"),
]
PROJECT_STATUS = [
    ("lead", "Lead"),
    ("proposal", "Proposal Submitted"),
    ("active", "Active"),
    ("under_amc", "Under AMC"),
    ("completed", "Completed"),
]
STATE_CATEGORIES = [
    ("todo", "To do"),
    ("active", "In progress"),
    ("done", "Done"),
]


class Workflow(models.Model):
    """A reusable set of board columns. Projects pick one, so boards are data-driven."""

    name = models.CharField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @classmethod
    def default(cls):
        return cls.objects.filter(is_default=True).first() or cls.objects.first()


class WorkflowState(models.Model):
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="states")
    name = models.CharField(max_length=60)
    slug = models.SlugField(max_length=60, blank=True)
    category = models.CharField(max_length=10, choices=STATE_CATEGORIES, default="todo")
    color = models.CharField(max_length=7, default="#64748b")
    order = models.PositiveIntegerField(default=0)
    wip_limit = models.PositiveIntegerField(default=0, help_text="0 = unlimited")

    class Meta:
        ordering = ["workflow", "order", "id"]
        unique_together = ("workflow", "slug")

    def __str__(self):
        return f"{self.workflow.name} / {self.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)


class Customer(models.Model):
    """A client organisation. Projects belong to one; people are attached per project."""

    name = models.CharField(max_length=160, unique=True)
    code = models.CharField(max_length=12, unique=True, blank=True, help_text="Short key, e.g. CNH")
    slug = models.SlugField(max_length=160, unique=True, blank=True)
    description = models.TextField(blank=True)
    industry = models.CharField(max_length=120, blank=True)
    website = models.URLField(blank=True)
    account_manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="accounts_managed"
    )
    primary_contact = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="customer_contacts"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="customers_created",
        help_text="Whoever created this record — kept separate from account_manager so the creator can always "
        "see it, even before it has a project or an assigned account manager.",
    )
    customer_name = models.CharField(max_length=160, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    color = models.CharField(max_length=7, default="#6366f1")
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("projects:customer_detail", args=[self.pk])

    def _free(self, field, candidates, maxlen):
        """The first candidate no other customer has (compared without regard to case); if every candidate
        is taken, the first one with 2, 3, ... added to it."""
        others = Customer.objects.exclude(pk=self.pk)
        candidates = [c[:maxlen] for c in candidates if c]
        for candidate in candidates:
            if not others.filter(**{f"{field}__iexact": candidate}).exists():
                return candidate
        base, n = candidates[0], 2
        while True:
            candidate = base[:maxlen - len(str(n))] + str(n)
            if not others.filter(**{f"{field}__iexact": candidate}).exists():
                return candidate
            n += 1

    def save(self, *args, **kwargs):
        if not self.code:
            # initials first (as before); if another customer already has that code, longer ones, then a number
            letters = "".join(ch for ch in self.name.upper() if ch.isalnum())
            initials = "".join(w[0] for w in self.name.split()[:3]).upper()
            self.code = self._free("code", [initials, letters[:3], letters[:4], letters[:6], "CUS"], 12)
        if not self.slug:
            self.slug = self._free("slug", [slugify(self.name) or "customer"], 160)
        super().save(*args, **kwargs)

    @property
    def initials(self):
        return self.code[:3].upper()

    def stats(self, projects=None):
        rows = self.projects.all() if projects is None else projects
        rows = list(rows)
        done = sum(1 for p in rows if p.status == "completed")
        progress = round(sum(p.progress for p in rows) / len(rows)) if rows else 0
        return {
            "projects": len(rows),
            "active": sum(1 for p in rows if p.status == "active"),
            "completed": done,
            "progress": progress,
            "overdue": sum(1 for p in rows if p.is_overdue),
        }


class Label(models.Model):
    name = models.CharField(max_length=40)
    color = models.CharField(max_length=7, default="#94a3b8")
    project = models.ForeignKey(
        "Project", null=True, blank=True, on_delete=models.CASCADE, related_name="own_labels"
    )

    class Meta:
        ordering = ["name"]
        unique_together = ("name", "project")

    def __str__(self):
        return self.name


def po_upload_to(instance, filename):
    stamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    return f"projects/{instance.pk or 'new'}/po/{stamp}-{filename}"


class Project(models.Model):
    """Projects nest: a parent project acts as a programme / portfolio."""

    key = models.CharField(max_length=12, unique=True)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    customer = models.ForeignKey(
        Customer, null=True, blank=True, on_delete=models.SET_NULL, related_name="projects"
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    status = models.CharField(max_length=12, choices=PROJECT_STATUS, default="lead")
    priority = models.PositiveSmallIntegerField(choices=PRIORITIES, default=3)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_projects"
    )
    team = models.ForeignKey(
        "accounts.Team", null=True, blank=True, on_delete=models.SET_NULL, related_name="projects"
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="ProjectMembership", related_name="projects"
    )
    workflow = models.ForeignKey(Workflow, null=True, blank=True, on_delete=models.SET_NULL, related_name="projects")
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    budget = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    color = models.CharField(max_length=7, default="#6366f1")
    # purchase order, asked for when the project goes Active; both optional
    po_number = models.CharField("PO number", max_length=100, blank=True)
    po_document = models.FileField("PO document", upload_to=po_upload_to, blank=True)
    archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["priority", "name"]

    def __str__(self):
        return f"{self.key} · {self.name}"

    def get_absolute_url(self):
        return reverse("projects:detail", args=[self.pk])

    def save(self, *args, **kwargs):
        if not self.key:
            base = "".join(w[0] for w in self.name.split()[:3]).upper() or "PRJ"
            key, n = base, 1
            while Project.objects.filter(key=key).exclude(pk=self.pk).exists():
                n += 1
                key = f"{base}{n}"
            self.key = key
        if not self.workflow_id:
            self.workflow = Workflow.default()
        super().save(*args, **kwargs)

    def descendant_ids(self):
        ids, frontier = {self.pk}, [self.pk]
        while frontier:
            frontier = [
                i
                for i in Project.objects.filter(parent_id__in=frontier).values_list("id", flat=True)
                if i not in ids
            ]
            ids.update(frontier)
        return ids

    @property
    def states(self):
        return self.workflow.states.all() if self.workflow_id else WorkflowState.objects.none()

    @property
    def progress(self):
        total = self.tasks.count()
        if not total:
            return 0
        done = self.tasks.filter(state__category="done").count()
        return round(done * 100 / total)

    @property
    def open_tasks(self):
        return self.tasks.exclude(state__category="done").count()

    @property
    def status_track(self):
        """Stepper data for the project card: each lifecycle stage marked done / current / next.
        The steps are PROJECT_STATUS in list order, so changing that list updates the stepper."""
        track = PROJECT_STATUS
        keys = [k for k, _ in track]
        current = keys.index(self.status) if self.status in keys else None
        steps = []
        for i, (k, label) in enumerate(track):
            if current is None:
                state = "next"
            else:
                state = "done" if i < current else ("current" if i == current else "next")
            steps.append({"number": i + 1, "label": label, "state": state})
        n = len(track)
        inset = 50 / n if n else 0  # the connecting line runs from the first dot's centre to the last's
        span = 100 - 2 * inset
        return {
            "steps": steps,
            "current": None if current is None else current + 1,
            "total": n,
            "inset": round(inset, 2),
            "span": round(span, 2),
            "fill": 0 if current is None or n < 2 else round(current * span / (n - 1), 2),
            "paused": current is None,
        }

    @property
    def po_filename(self):
        """The PO file's name as uploaded, without its storage folder and timestamp prefix."""
        if not self.po_document:
            return ""
        name = self.po_document.name.rsplit("/", 1)[-1]
        return name.split("-", 2)[-1] if name[:8].isdigit() else name

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < timezone.localdate() and self.status != "completed")

    @property
    def days_left(self):
        if not self.due_date:
            return None
        return (self.due_date - timezone.localdate()).days


SIDES = [
    ("client", "Client side"),
    ("internal", "Our organisation"),
]


class ProjectMembership(models.Model):
    """A person on the project, from either side, with their brief and standing remarks."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.ForeignKey(
        "accounts.Role", null=True, blank=True, on_delete=models.SET_NULL, related_name="project_memberships"
    )
    side = models.CharField(max_length=10, choices=SIDES, default="internal")
    position = models.CharField(max_length=120, blank=True, help_text="Title on this project, e.g. Product Owner")
    role_description = models.TextField(blank=True, help_text="What this person's role is on this project")
    responsibility = models.TextField(blank=True, help_text="What they are accountable for")
    remarks = models.TextField(blank=True, help_text="Free-form remarks, kept with the person")
    is_primary_contact = models.BooleanField(default=False)
    allocation = models.PositiveSmallIntegerField(default=100, help_text="% of capacity")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("project", "user")
        ordering = ["side", "-is_primary_contact", "user__first_name"]

    def __str__(self):
        return f"{self.user} @ {self.project.key} ({self.get_side_display()})"

    @property
    def label(self):
        parts = [self.user.display_name]
        if self.position:
            parts.append(self.position)
        parts.append(self.get_side_display())
        return " · ".join(parts)


class Milestone(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="milestones")
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    due_date = models.DateField(null=True, blank=True)
    completed = models.BooleanField(default=False)

    class Meta:
        ordering = ["due_date", "name"]

    def __str__(self):
        return self.name


class Task(models.Model):
    """Tasks nest arbitrarily deep via `parent` (epic > story > subtask)."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    milestone = models.ForeignKey(
        Milestone, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks"
    )
    title = models.CharField(max_length=240)
    description = models.TextField(blank=True)
    state = models.ForeignKey(WorkflowState, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    priority = models.PositiveSmallIntegerField(choices=PRIORITIES, default=3)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks"
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reported_tasks"
    )
    labels = models.ManyToManyField(Label, blank=True, related_name="tasks")
    blocked_by = models.ManyToManyField("self", blank=True, symmetrical=False, related_name="blocks")
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    estimate_hours = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    spent_hours = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "priority", "-created_at"]
        indexes = [
            models.Index(fields=["project", "state"]),
            models.Index(fields=["assignee"]),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("projects:task_detail", args=[self.pk])

    def save(self, *args, **kwargs):
        if not self.state_id and self.project_id:
            self.state = self.project.states.first()
        super().save(*args, **kwargs)

    @property
    def is_done(self):
        return bool(self.state_id and self.state.category == "done")

    @property
    def is_overdue(self):
        return bool(self.due_date and self.due_date < timezone.localdate() and not self.is_done)

    @property
    def progress(self):
        kids = list(self.children.all())
        if kids:
            done = sum(1 for k in kids if k.is_done)
            return round(done * 100 / len(kids))
        return 100 if self.is_done else 0


DOC_KINDS = [
    ("tech_stack", "Tech Stack"),
    ("dev_instructions", "Development Instructions"),
    ("build_plan", "Build Plan"),
]

# Starter content for a brand-new, never-edited document of each kind. This is the
# organisation's baseline stack/guideline, condensed from dev_guid/DRF Standard
# Documentation.pdf (backend) and dev_guid/Angular_Code_Standards_Guidelines.docx (frontend).
# NOTE: rendered with the "nl2br" markdown extension — keep every paragraph/list item on
# ONE physical line below, or it will render with a stray line break.
DOC_DEFAULT_CONTENT = {
    "tech_stack": """# Tech Stack

## Backend
- Django + Django REST Framework (DRF)
- PostgreSQL as the primary database

## Frontend
- Angular (v15+) with PrimeNG

## Infrastructure
- Docker & Docker Compose for local development and deployment
- Application source code bind-mounted into containers (edit on host, runs live in the container)
- Database and cache data kept in named Docker volumes, not the container's writable layer
- One `docker-compose.yml` at the repository root — not one per app, not one per directory
- Django (`backend/`) and Angular (`frontend/`) kept in separate top-level directories, each independently runnable
""",
    "dev_instructions": """# Development Guidelines

## Repository layout
- Single root-level `docker-compose.yml` orchestrating both services — never a separate compose file per app.
- Keep the Django backend and the Angular frontend in separate top-level directories (`backend/` and `frontend/`), each with its own dependency files (`requirements.txt`/`manage.py` for backend, `package.json`/`angular.json` for frontend).
- `docker-compose.yml` lives outside both `backend/` and `frontend/`, at the repository root.

## Environment
- Develop and deploy everything inside Docker / Docker Compose.
- Keep application source code in a bind-mounted volume for both services so host edits are reflected immediately in the running containers.
- Keep database data and any cache data in named Docker volumes so it survives `docker compose down` and rebuilds.
- Use PostgreSQL as the primary database in every environment — never substitute SQLite, including for local development.
- Never hardcode secrets (`DEBUG`, `SECRET_KEY`, DB credentials, API keys); load them from `.env`.

## Backend (Django + DRF)

### Architecture
- Serializer = validation and data transformation only.
- ViewSet = business logic, orchestration, role checks, DB writes.
- Model = pure data structure — no business logic, no heavy DB operations; only `__str__` and small helpers are allowed on a model.

### Models
- Every model inherits an abstract `BaseModel` with `created_at`, `updated_at`, `is_active`.
- Use the correct field type for the data: `DecimalField` for money (never `FloatField`), `CharField` for short text, `TextField` for long text.
- Prefer `blank=True` for optional strings; reserve `null=True` for genuinely nullable non-string fields at the DB level.
- Choose `on_delete` deliberately: `PROTECT` for data that must never silently disappear, `CASCADE` for owned child data, `SET_NULL` for optional references.
- Use a foreign key to a master/reference table once a list can grow or needs extra fields; use `choices` only for small, static, rarely-changing sets.
- Add indexes for fields used in frequent filters and for foreign keys; use `unique_together`/`UniqueConstraint` to prevent duplicates.
- Prefer soft delete (`is_active=False`) over hard deletion, and filter on it everywhere reads happen.

### Migrations
- Every model change gets a migration; review it (and its SQL via `sqlmigrate`) before applying.
- Rename auto-generated migration files to something meaningful (`--name add_project_status`).
- Never edit an already-applied or committed migration — only ever create a new one.
- Adding a required field to a table with existing rows is a 3-step rollout: add it nullable, backfill via a `RunPython` data migration, then make it non-nullable in a later migration.
- Never reset migrations or the database as a shortcut, outside a disposable local environment.

### API
- Version every endpoint (`/api/v1/...`); never make a breaking change to a version already shipped.
- Enforce JWT authentication and server-side role/permission checks on every endpoint — UI guards are not a security boundary.
- Apply throttling on public and write endpoints to prevent abuse.
- Use the standard paginated response shape (`count`/`next`/`previous`/`results`) and support filtering/search/ordering on list endpoints.
- Wrap multi-step state changes in `transaction.atomic()`.
- Use `select_related`/`prefetch_related` to avoid N+1 queries; never issue a query inside a loop.
- Return errors as `{"code": "...", "message": "..."}`, not framework-default HTML or plain text.
- Document the API with `drf-spectacular` (auto Swagger/OpenAPI).
- Keep viewsets from growing past a screenful of logic — split large methods into private helpers.

## Frontend (Angular v15+ with PrimeNG)

### Project structure
- `src/app/features/` for feature components and modules.
- `src/app/core/` for cross-cutting concerns: `services/` (API/auth services, interceptors, guards), `layout/` (header, sidebar, shell), `pipes/`.
- Routing in `app-routing.module.ts`; environment config under `environments/`.

### Class structure
- Order class members consistently: properties, constructor, lifecycle hooks (`ngOnInit`, `ngOnDestroy`, ...), public methods, then private helpers.

### Components & services
- Components stay small and focused on presentation only; business logic and API calls live in services.
- Avoid direct DOM manipulation and circular dependencies between services.

### Auth & security
- Prefer HttpOnly cookies for token storage; if that isn't possible, use `localStorage`/`sessionStorage` through an interceptor — never pass tokens around directly in service code.
- Guard every route that requires authentication or a specific role.
- Sanitize any dynamically-rendered HTML; serve over HTTPS only.

### Performance & quality
- Use `trackBy` in every `*ngFor` over a non-trivial list; avoid unnecessary change detection.
- Lazy-load every feature module.
- Minimum 70% test coverage.
- Every module has a `README.md`; public services are documented.

---
*Backend standards condensed from `dev_guid/DRF Standard Documentation.pdf`; frontend standards condensed from `dev_guid/Angular_Code_Standards_Guidelines.docx`.*
""",
}


class ProjectDocument(models.Model):
    """A hand-edited markdown doc scoped to a project (tech stack, dev instructions, ...)."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField(max_length=20, choices=DOC_KINDS)
    content = models.TextField(blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    is_frozen = models.BooleanField(default=False, help_text="Locked as final — no further edits")
    frozen_at = models.DateTimeField(null=True, blank=True)
    frozen_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("project", "kind")
        ordering = ["kind"]

    def __str__(self):
        return f"{self.project.key} · {self.get_kind_display()}"

    @property
    def filename(self):
        return f"{self.project.key}-{self.kind}.md"


class Comment(models.Model):
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.CASCADE, related_name="comments")
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="comments")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.author}: {self.body[:40]}"


class Activity(models.Model):
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.CASCADE, related_name="activities"
    )
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.CASCADE, related_name="activities")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="activities"
    )
    verb = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "activities"

    def __str__(self):
        return f"{self.actor} {self.verb}"

    @classmethod
    def log(cls, actor, verb, project=None, task=None):
        return cls.objects.create(
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            verb=verb,
            project=project or (task.project if task else None),
            task=task,
        )


class ProjectLink(models.Model):
    """A website or app link for a project (staging, UAT, an admin panel …) with optional sample login
    credentials, so anyone on the project can open it and sign in. The password is stored encrypted."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="links")
    title = models.CharField(max_length=120)
    url = models.URLField(max_length=500)
    username = models.CharField(max_length=200, blank=True)
    password = models.CharField(max_length=500, blank=True, help_text="Stored encrypted; never shown in lists")
    notes = models.TextField(blank=True)
    # "Open through Planwise": served at /sites/<site_name>/ by the gateway container, for people
    # outside the VPN. Only signed-in users who can see the project get through (nginx asks Planwise).
    via_gateway = models.BooleanField(default=False)
    site_name = models.CharField(max_length=32, blank=True,
                                 help_text="Address under /sites/, e.g. gepdec_solar (lowercase, digits, - and _)")
    gateway_options = models.JSONField(default=dict, blank=True,
                                       help_text="rewrite_html, rewrite_js, isolate_storage, insecure, sync_tokens, mounts")
    gateway_synced_at = models.DateTimeField(null=True, blank=True)
    gateway_error = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["site_name"], condition=~models.Q(site_name=""),
                                    name="projectlink_site_name_unique"),
        ]

    def __str__(self):
        return f"{self.project_id}: {self.title}"

    @property
    def target_origin(self):
        """scheme://host:port of the link: what the gateway forwards to."""
        from urllib.parse import urlsplit

        parts = urlsplit(self.url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def open_url(self):
        """Where "Open site" goes: through Planwise at /sites/<name>/ (same path) or the link itself."""
        if not (self.via_gateway and self.site_name):
            return self.url
        from urllib.parse import urlsplit

        parts = urlsplit(self.url)
        path = parts.path or "/"
        return f"/sites/{self.site_name}{path}" + (f"?{parts.query}" if parts.query else "")

    def set_password(self, raw):
        from apps.vms import crypto

        self.password = crypto.encrypt(raw)

    def get_password(self):
        from apps.vms import crypto

        return crypto.decrypt(self.password)


def project_file_upload_to(instance, filename):
    # an unguessable folder per file, so the path alone never gives access (downloads are checked in the view)
    import uuid

    return f"projects/{instance.project_id}/files/{uuid.uuid4().hex}/{filename}"


class ProjectFile(models.Model):
    """A document uploaded against a project (contracts, specs, sign-offs …). Anyone who can open the
    project can see and download it; downloads always go through a permission check."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="files")
    file = models.FileField(upload_to=project_file_upload_to, max_length=500)
    original_name = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField(default=0)
    note = models.CharField(max_length=300, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"{self.project.key} · {self.original_name}"

    @property
    def extension(self):
        return self.original_name.rsplit(".", 1)[-1].lower() if "." in self.original_name else ""

    @property
    def can_preview(self):
        """Safe to show in the browser tab: PDFs and plain raster images only (never HTML or SVG)."""
        return self.extension in {"pdf", "png", "jpg", "jpeg", "gif", "webp"}
