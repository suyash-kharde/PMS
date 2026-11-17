from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

SCOPE_OWN = "own"
SCOPE_TEAM = "team"
SCOPE_ALL = "all"
SCOPES = [
    (SCOPE_OWN, "Own records only"),
    (SCOPE_TEAM, "Own team / subordinates"),
    (SCOPE_ALL, "Everything"),
]
SCOPE_RANK = {SCOPE_OWN: 1, SCOPE_TEAM: 2, SCOPE_ALL: 3}


class Capability(models.Model):
    """An atomic, data-defined permission. New ones can be added at runtime."""

    code = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=120)
    category = models.CharField(max_length=40, default="general")
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["category", "code"]
        verbose_name_plural = "capabilities"

    def __str__(self):
        return self.code


class Role(models.Model):
    """A user type. Roles form a tree; a child role inherits its parent's grants."""

    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True, blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    rank = models.PositiveIntegerField(default=100, help_text="Lower = more senior")
    color = models.CharField(max_length=7, default="#6366f1")
    description = models.TextField(blank=True)
    is_system = models.BooleanField(default=False, help_text="Cannot be deleted from the UI")
    capabilities = models.ManyToManyField(Capability, through="RoleCapability", related_name="roles")

    class Meta:
        ordering = ["rank", "name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def ancestors(self):
        """Self + parents, most senior last."""
        chain, seen, node = [], set(), self
        while node and node.pk not in seen:
            seen.add(node.pk)
            chain.append(node)
            node = node.parent
        return chain

    @property
    def depth(self):
        return len(self.ancestors()) - 1

    def descendant_ids(self):
        ids, frontier = {self.pk}, [self.pk]
        while frontier:
            frontier = list(Role.objects.filter(parent_id__in=frontier).values_list("id", flat=True))
            frontier = [i for i in frontier if i not in ids]
            ids.update(frontier)
        return ids


class RoleCapability(models.Model):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_caps")
    capability = models.ForeignKey(Capability, on_delete=models.CASCADE, related_name="role_caps")
    scope = models.CharField(max_length=8, choices=SCOPES, default=SCOPE_OWN)

    class Meta:
        unique_together = ("role", "capability")

    def __str__(self):
        return f"{self.role}:{self.capability}={self.scope}"


class Team(models.Model):
    """Org unit. Teams nest, giving the organisation hierarchy."""

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children"
    )
    lead = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="teams_led"
    )
    color = models.CharField(max_length=7, default="#0ea5e9")
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def descendant_ids(self):
        ids, frontier = {self.pk}, [self.pk]
        while frontier:
            frontier = [
                i
                for i in Team.objects.filter(parent_id__in=frontier).values_list("id", flat=True)
                if i not in ids
            ]
            ids.update(frontier)
        return ids


class User(AbstractUser):
    role = models.ForeignKey(Role, null=True, blank=True, on_delete=models.SET_NULL, related_name="users")
    team = models.ForeignKey(Team, null=True, blank=True, on_delete=models.SET_NULL, related_name="members")
    manager = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reports"
    )
    customer = models.ForeignKey(
        "projects.Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="users"
    )
    title = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    color = models.CharField(max_length=7, default="#64748b")
    description = models.TextField(blank=True)
    responsibility = models.TextField(blank=True)
    remark = models.TextField(blank=True)
    last_active = models.DateTimeField(null=True, blank=True)
    # multi-factor authentication (authenticator app); see mfa.py. The secret is stored encrypted.
    mfa_required = models.BooleanField(
        "Require multi-factor authentication", default=False, db_default=False,
        help_text="This person must enter a code from an authenticator app every time they sign in "
                  "(when the Security page is set to 'Only people I have ticked'). Resetting is on this page.",
    )
    mfa_secret = models.CharField(max_length=200, blank=True, editable=False, db_default="")
    mfa_confirmed_at = models.DateTimeField(null=True, blank=True, editable=False)
    mfa_backup_codes = models.TextField(blank=True, editable=False, db_default="")
    mfa_last_step = models.BigIntegerField(default=0, editable=False, db_default=0)
    must_change_password = models.BooleanField(
        default=True,
        help_text="Asked to set their own password once, at their next sign-in. Cleared when they change it.",
    )

    class Meta:
        ordering = ["first_name", "username"]

    DEFAULT_ROLE_SLUG = "member"
    CLIENT_ROLE_SLUG = "customer"
    ADMIN_ROLE_SLUGS = ("admin", "devadmin")

    @property
    def is_site_admin(self):
        """Administrator or Devadmin role (or a Django superuser): the people the "admin only" parts of Planwise
        are for. Devadmin is the higher level and holds every permission as well."""
        return bool(self.is_superuser or getattr(self.role, "slug", "") in self.ADMIN_ROLE_SLUGS)

    @property
    def is_client_login(self):
        """A client-side login: has the Customer role (or a customer link and no role at all). Staff who are
        merely linked to a customer record — a Project Manager at SDF Group, say — are NOT client logins."""
        if self.is_superuser:
            return False
        if self.role_id is None:
            return self.customer_id is not None
        return getattr(self.role, "slug", "") == self.CLIENT_ROLE_SLUG

    def save(self, *args, **kwargs):
        """Staff accounts saved without a role get the Member role (change it later from People → Edit).
        Client-side logins (linked to a customer) are left as they are."""
        if self.role_id is None and self.customer_id is None:
            self.role = Role.objects.filter(slug=self.DEFAULT_ROLE_SLUG).first()
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def initials(self):
        parts = [p for p in (self.first_name, self.last_name) if p]
        if not parts:
            return self.username[:2].upper()
        return "".join(p[0] for p in parts).upper()

    def subordinate_ids(self, include_self=True):
        """Every user below this one in the manager chain."""
        ids = {self.pk} if include_self else set()
        frontier = [self.pk]
        while frontier:
            frontier = [
                i
                for i in User.objects.filter(manager_id__in=frontier).values_list("id", flat=True)
                if i not in ids
            ]
            ids.update(frontier)
        return ids


class UserCapability(models.Model):
    """Per-user override on top of whatever the role tree grants."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="cap_overrides")
    capability = models.ForeignKey(Capability, on_delete=models.CASCADE, related_name="user_overrides")
    scope = models.CharField(max_length=8, choices=SCOPES, default=SCOPE_OWN)
    deny = models.BooleanField(default=False, help_text="Revoke instead of grant")

    class Meta:
        unique_together = ("user", "capability")

    def __str__(self):
        verb = "deny" if self.deny else self.scope
        return f"{self.user}:{self.capability}={verb}"


class UserDayActivity(models.Model):
    """Time a person spent using Planwise on one (local) day, fed by CapabilityMiddleware:
    gaps of up to ACTIVE_GAP_SECONDS between their page loads count as time worked."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="day_activity")
    date = models.DateField()
    active_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-date"]
        constraints = [models.UniqueConstraint(fields=["user", "date"], name="user_day_activity_unique")]

    def __str__(self):
        return f"{self.user} {self.date}: {self.active_seconds}s"


class PageVisit(models.Model):
    """One page a person opened (a project, a tab, a ticket, ...), for their "work on this day"
    view. Written by CapabilityMiddleware; the same page re-opened within a minute is skipped."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="page_visits")
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    path = models.CharField(max_length=300)
    title = models.CharField(max_length=200, blank=True)
    visited_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["visited_at"]
        indexes = [models.Index(fields=["user", "visited_at"], name="pagevisit_user_time_idx")]

    def __str__(self):
        return f"{self.user} {self.path} @ {self.visited_at:%Y-%m-%d %H:%M}"



class SiteSetting(models.Model):
    """A small admin-editable setting, one row per key (see site_settings.py). Today: the auto sign-out time."""

    key = models.CharField(max_length=64, unique=True)
    value = models.CharField(max_length=200, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.key} = {self.value}"
