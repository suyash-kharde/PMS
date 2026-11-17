from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, Lower, NullIf
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.audit.services import log as audit_log
from apps.projects.selectors import visible_users

from . import mfa, otp, site_settings
from .actions import action_counts
from .forms import CapabilityForm, RoleForm, SignupForm, TeamForm, UserForm
from .models import (
    SCOPE_ALL, SCOPE_RANK, SCOPES, Capability, PageVisit, Role, RoleCapability, Team, User, UserCapability, UserDayActivity,
)
from .permissions import effective_caps, has_cap, invalidate, require, role_scopes

# role_list display only — a computed summary, not a stored field, so it can't drift out of
# sync with the real grants and never affects what has_cap()/require() actually enforce.
# icon_key selects one of the outline SVGs defined in role_list.html; colors are a fixed
# pastel-tile palette (not theme variables) to match a consistent enterprise look.
ROLE_ICON_KEYS = {
    "admin": "shield", "bda": "handshake", "director": "compass", "manager": "briefcase",
    "aiops": "cpu", "lead": "users", "member": "user", "guest": "eye",
}
ROLE_ICON_COLORS = {
    "admin": ("#eef2ff", "#4f46e5"),
    "bda": ("#f0fdfa", "#0d9488"),
    "director": ("#fff7ed", "#ea580c"),
    "manager": ("#eff6ff", "#2563eb"),
    "aiops": ("#faf5ff", "#9333ea"),
    "lead": ("#fdf2f8", "#db2777"),
    "member": ("#f1f5f9", "#475569"),
    "guest": ("#f8fafc", "#64748b"),
}
_MUTATE_VERBS = ("create", "edit", "delete", "manage", "generate", "triage", "invite", "move", "process")


def _permission_summary(role):
    caps = list(role.role_caps.all())
    n = len(caps)
    if n == 0:
        return "No access", "badge"
    frac_all = sum(1 for c in caps if c.scope == SCOPE_ALL) / n
    has_mutate = any(any(v in c.capability.code for v in _MUTATE_VERBS) for c in caps)
    if frac_all >= 0.6:
        return "Full access", "badge-active"
    if not has_mutate:
        return "Read only", "badge"
    if frac_all >= 0.15:
        return "Custom", "badge-planning"
    return "Limited", "badge-completed"


def signup(request):
    from django.conf import settings

    if not settings.ALLOW_SIGNUP:  # closed: administrators create accounts (People, New user)
        raise Http404("Sign-up is closed.")
    if request.user.is_authenticated:
        return redirect("core:dashboard")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if otp.enabled() and not (form.cleaned_data.get("email") or "").strip():
            form.add_error("email", "An email address is required: your login codes are sent to it.")
            return render(request, "accounts/signup.html", {"form": form})
        user = form.save(commit=False)
        user.role = Role.objects.filter(slug="member").first() or Role.objects.order_by("-rank").first()
        user.save()
        user.backend = "django.contrib.auth.backends.ModelBackend"
        if mfa.required_for(user):
            mfa.begin(request, user, next_url=redirect("core:dashboard").url, backend=user.backend)
            return redirect("accounts:login_mfa")
        if otp.applies_to(user):  # a new account also proves its mailbox with a code before it is signed in
            ok, error = otp.begin(request, user, next_url=redirect("core:dashboard").url, backend=user.backend)
            if ok:
                return redirect("accounts:login_otp")
            return redirect(f"{reverse('accounts:login')}?otp=failed")
        login(request, user)
        messages.success(request, "Welcome aboard.")
        return redirect("core:dashboard")
    return render(request, "accounts/signup.html", {"form": form})


class OtpLoginView(auth_views.LoginView):
    """The normal sign-in page. When login codes are on and apply to this person, a correct password does not
    sign them in: it emails a code and sends them to the code page (see login_otp)."""

    template_name = "accounts/login.html"
    redirect_authenticated_user = True
    otp_error = ""

    def form_valid(self, form):
        user = form.get_user()
        if mfa.required_for(user):  # an authenticator app code replaces the email code for this person
            mfa.begin(self.request, user, next_url=self.get_success_url(), backend=getattr(user, "backend", ""))
            return redirect("accounts:login_mfa")
        if not otp.applies_to(user):
            return super().form_valid(form)
        ok, error = otp.begin(self.request, user, next_url=self.get_success_url(), backend=getattr(user, "backend", ""))
        if not ok:
            self.otp_error = error
            return self.form_invalid(form)
        return redirect("accounts:login_otp")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["otp_error"] = self.otp_error
        context["otp_notice"] = {
            "expired": "Your login code expired. Please sign in again.",
            "locked": "Too many wrong codes. Please sign in again.",
            "failed": "We couldn't email your login code. Please try signing in again.",
            "required": "Please sign in again: a login code is needed for every sign-in.",
            "mfa_locked": "Too many wrong codes. Please sign in again.",
        }.get(self.request.GET.get("otp", ""), "")
        return context


def login_mfa(request):
    """Sign-in step for people who need an authenticator app. First time: scan the QR code and type one code to
    prove the app works. After that: type the current code (or a backup code)."""
    from django.conf import settings
    from django.utils.http import url_has_allowed_host_and_scheme

    from apps.vms import crypto

    back = reverse("accounts:login")
    data = mfa.pending(request)
    if data is None:
        return redirect(f"{back}?otp=expired")
    user = User.objects.filter(pk=data["uid"], is_active=True).first()
    if user is None:
        mfa.clear(request)
        return redirect(back)
    setup_secret = data.get("setup_secret", "")
    secret = setup_secret or crypto.decrypt(user.mfa_secret)
    if not secret:  # the saved secret can't be read: never fall back to enrolling a new one on a password alone
        mfa.clear(request)
        return render(request, "accounts/login_mfa.html", {"broken": True})
    error = ""
    if request.method == "POST":
        code = request.POST.get("code", "")
        step = mfa.verify(secret, code, 0 if setup_secret else user.mfa_last_step)
        used_backup = False
        if not step and not setup_secret:
            used_backup = mfa.use_backup_code(user, code)
        if step or used_backup:
            codes = []
            if setup_secret:
                codes = mfa.confirm_setup(user, setup_secret, step)
            elif step:
                user.mfa_last_step = step
                user.save(update_fields=["mfa_last_step"])
            mfa.clear(request)
            target = data.get("next") or settings.LOGIN_REDIRECT_URL
            if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                target = settings.LOGIN_REDIRECT_URL
            login(request, user, backend=data.get("backend") or None)
            request.session[otp.VERIFIED_KEY] = True  # after login(): that call starts a fresh session key
            if setup_secret:
                audit_log(user, "edit", "users", target=user.get_username(), details="authenticator app set up")
            if codes:
                request.session["mfa_backup_codes"] = codes
                request.session["mfa_next"] = target
                return redirect("accounts:mfa_backup_codes")
            return redirect(target)
        audit_log(None, "login", "auth", target=user.get_username(), status="failed", details="wrong authenticator code")
        if mfa.fail(request, data):
            return redirect(f"{back}?otp=mfa_locked")
        error = "That code is not right. Check that your phone's time is correct and try the newest code."
    return render(request, "accounts/login_mfa.html", {
        "setup": bool(setup_secret), "secret": setup_secret, "error": error,
        "uri": mfa.provisioning_uri(setup_secret, user.get_username()) if setup_secret else "",
        "username": user.get_username(),
    })


@login_required
def mfa_backup_codes(request):
    """Shown once, right after setup: the backup codes to keep for a lost phone."""
    codes = request.session.pop("mfa_backup_codes", None)
    target = request.session.pop("mfa_next", None) or "/"
    if not codes:
        return redirect(target)
    return render(request, "accounts/mfa_backup_codes.html", {"codes": codes, "next": target})


@login_required
@require_POST
def mfa_reset(request, pk):
    """Clear someone's authenticator (lost phone): they scan a new QR code at their next sign-in."""
    require(request.user, "security.manage")
    person = get_object_or_404(User, pk=pk)
    mfa.reset(person)
    audit_log(request.user, "edit", "users", target=person.get_username(), details="authenticator reset")
    messages.success(request, f"{person.display_name}'s authenticator was reset. They will set up a new one at their next sign-in.")
    return redirect("accounts:user_edit", person.pk)


def login_otp(request):
    """Second step of sign-in: type the code that was emailed. Not available without a correct password first."""
    from django.conf import settings
    from django.utils.http import url_has_allowed_host_and_scheme

    back = reverse("accounts:login")
    data = otp.pending(request)
    if data is None:
        return redirect(f"{back}?otp=expired")
    user = User.objects.filter(pk=data["uid"], is_active=True).first()
    if user is None:
        otp.clear(request)
        return redirect(back)
    error = notice = ""
    if request.method == "POST":
        if request.POST.get("action") == "resend":
            ok, message = otp.resend(request, data, user)
            if ok:
                notice = "A new code has been sent."
            else:
                error = message
                if otp.pending(request) is None:
                    return redirect(f"{back}?otp=expired")
        else:
            result = otp.check(request, data, request.POST.get("code", ""))
            if result == "ok":
                otp.clear(request)
                target = data.get("next") or settings.LOGIN_REDIRECT_URL
                if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                    target = settings.LOGIN_REDIRECT_URL
                login(request, user, backend=data.get("backend") or None)
                request.session[otp.VERIFIED_KEY] = True  # after login(): that call starts a fresh session key
                return redirect(target)
            audit_log(None, "login", "auth", target=user.get_username(), status="failed", details="wrong login code")
            if result == "locked":
                return redirect(f"{back}?otp=locked")
            error = "That code is not right."
        data = otp.pending(request) or data
    return render(request, "accounts/login_otp.html", {
        "masked": otp.mask(user.email), "error": error, "notice": notice,
        "ttl_minutes": settings.LOGIN_OTP_TTL_SECONDS // 60, "auth_page": True,
    })


@login_required
def security_settings(request):
    """Page for roles holding the Security permission (security.manage): how long someone can sit idle before Planwise signs them out. People who keep working
    are never signed out; only a stretch of no activity counts."""
    require(request.user, "security.manage")
    current = site_settings.idle_timeout_seconds()
    if request.method == "POST" and request.POST.get("action") == "mfa":
        choice = request.POST.get("mode", "")
        if choice not in site_settings.MFA_MODES:
            messages.error(request, "Choose one of the options.")
            return redirect("accounts:security_settings")
        before = site_settings.mfa_mode()
        site_settings.set_mfa_mode(choice, request.user)
        audit_log(request.user, "edit", "org", target="Setting › Multi-factor authentication", details=f"{before} -> {choice}")
        messages.success(request, "Multi-factor authentication choice saved. People who need it and have not set it up "
                                  "are asked to the next time they sign in.")
        return redirect("accounts:security_settings")
    if request.method == "POST":
        presets = {30 * 60, 60 * 60, 2 * 3600, 5 * 3600, 8 * 3600, 12 * 3600, 24 * 3600}
        try:
            if request.POST.get("preset"):
                seconds = int(request.POST["preset"])
                if seconds not in presets:
                    raise ValueError
            else:
                amount = float(request.POST.get("amount", ""))
                unit = {"minutes": 60, "hours": 3600, "days": 86400}[request.POST.get("unit", "")]
                seconds = int(round(amount * unit))
        except (ValueError, KeyError):
            messages.error(request, "Enter a number, for example 5 hours.")
            return redirect("accounts:security_settings")
        if not site_settings.MIN_SECONDS <= seconds <= site_settings.MAX_SECONDS:
            messages.error(request, "Choose a time between 5 minutes and 7 days.")
            return redirect("accounts:security_settings")
        site_settings.set_idle_timeout_seconds(seconds, request.user)
        audit_log(request.user, "edit", "org", target="Setting › Auto sign-out",
                  details=f"{site_settings.describe(current)} -> {site_settings.describe(seconds)} of inactivity")
        messages.success(request, f"Auto sign-out is now {site_settings.describe(seconds)} of inactivity. "
                                  "It applies to everyone from their next click; people who keep working stay signed in.")
        return redirect("accounts:security_settings")
    return render(request, "accounts/security_settings.html", {
        "mfa_mode": site_settings.mfa_mode(), "mfa_forced_off": mfa.settings.MFA_FORCE_OFF,
        "mfa_ticked": User.objects.filter(is_active=True, mfa_required=True).count(),
        "mfa_set_up": User.objects.filter(is_active=True, mfa_confirmed_at__isnull=False).count(),
        "current": current, "current_text": site_settings.describe(current),
        "presets": [(1800, "30 minutes"), (3600, "1 hour"), (7200, "2 hours"), (18000, "5 hours"),
                    (28800, "8 hours"), (43200, "12 hours"), (86400, "24 hours")],
        "min_text": site_settings.describe(site_settings.MIN_SECONDS), "max_text": site_settings.describe(site_settings.MAX_SECONDS),
    })


@login_required
def password_change(request):
    """Change your own password. Everyone is sent here once (User.must_change_password) until they do."""
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.forms import PasswordChangeForm
    from django.utils.http import url_has_allowed_host_and_scheme

    forced = request.user.must_change_password
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = ""
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        user.must_change_password = False
        user.save(update_fields=["must_change_password"])
        update_session_auth_hash(request, user)  # stay signed in
        audit_log(user, "password_change", "auth", target=user,
                  details="first required change" if forced else "changed own password")
        messages.success(request, "Your password has been changed.")
        return redirect(next_url or "core:dashboard")
    return render(request, "accounts/password_change.html",
                  {"form": form, "forced": forced, "next": next_url, "auth_page": True})


# --------------------------------------------------------------------------- users
# "Solved" = a ticket assigned to the person that is now Resolved or Closed. Rejected is not solved
# (it means won't do / not a bug / duplicate), and a re-opened ticket stops counting.
SOLVED_STATUSES = ("resolved", "closed")

# The three ticket numbers on the People cards: kind -> (ticket column pointing at the person, extra filter).
# The count on a card and the list behind it both read this, so they can never disagree.
TICKET_KINDS = {
    "raised": ("reporter_id", {}),
    "assigned": ("assignee_id", {}),
    "solved": ("assignee_id", {"status__in": SOLVED_STATUSES}),
}


def _ticket_count(kind):
    """A per-person count of tickets, as a subquery so several of them can sit on one query without the
    joins multiplying each other's rows."""
    from apps.tickets.models import Ticket

    field, extra = TICKET_KINDS[kind]
    rows = (Ticket.objects.filter(**{field: OuterRef("pk")}, **extra)
            .order_by().values(field).annotate(n=Count("pk")).values("n"))
    return Coalesce(Subquery(rows, output_field=IntegerField()), 0)


@login_required
def user_list(request):
    require(request.user, "user.view")
    qs = visible_users(request.user).annotate(
        tickets_raised=_ticket_count("raised"),
        tickets_assigned=_ticket_count("assigned"),
        tickets_solved=_ticket_count("solved"),
    ).order_by(  # A -> Z by the name shown on the card (username when no first name is set)
        Lower(Coalesce(NullIf("first_name", Value("")), "username")), Lower("last_name"), Lower("username")
    )
    q = request.GET.get("q", "").strip()
    role = request.GET.get("role", "")
    team = request.GET.get("team", "")
    if q:
        qs = qs.filter(
            Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q)
        )
    if role:
        qs = qs.filter(role_id=role)
    if team:
        qs = qs.filter(team_id=team)
    context = {
        "users": list(qs),
        "q": q,
        "role": role,
        "team": team,
        "roles": Role.objects.all(),
        "teams": Team.objects.all(),
    }
    return render(request, "accounts/user_list.html", context)


TICKET_LIST_LIMIT = 300


@login_required
def user_tickets(request, pk, kind):
    """The tickets behind one of the numbers on a People card, for the pop-up on that page. The card's
    number counts every ticket; the list only holds the ones the viewer may open, and says when some are
    left out, so ticket titles from projects they can't see are never exposed."""
    require(request.user, "user.view")
    if kind not in TICKET_KINDS:
        raise Http404("Unknown list")
    person = get_object_or_404(visible_users(request.user), pk=pk)
    from apps.tickets.models import Ticket
    from apps.tickets.selectors import visible_tickets

    field, extra = TICKET_KINDS[kind]
    flt = {field: person.pk, **extra}
    total = Ticket.objects.filter(**flt).count()
    order = {"raised": "-created_at", "assigned": "-created_at", "solved": "-updated_at"}[kind]
    qs = visible_tickets(request.user).filter(**flt).order_by(order, "-pk")
    shown = qs.count()
    tickets = []
    for t in qs[:TICKET_LIST_LIMIT]:
        done = t.resolved_at or t.closed_at
        stamp = done if (kind == "solved" and done) else t.created_at
        tickets.append({
            "key": t.key, "title": t.title, "url": t.get_absolute_url(), "status": t.get_status_display(),
            "state": t.status, "project": t.project.key, "priority": t.get_priority_display(),
            "date": timezone.localtime(stamp).strftime("%d %b %Y"),
            "assignee": t.assignee.display_name if (kind == "raised" and t.assignee_id) else "",
        })
    return JsonResponse({
        "person": person.display_name, "kind": kind, "total": total, "shown": shown, "tickets": tickets,
        "truncated": shown > TICKET_LIST_LIMIT,
    })


@login_required
def user_detail(request, pk):
    person = get_object_or_404(User, pk=pk)
    if not has_cap(request.user, "user.view", person) and person.pk != request.user.pk:
        require(request.user, "user.view", person)
    context = {
        "person": person,
        "caps": sorted(effective_caps(person).items()),
        "reports": person.reports.select_related("role"),
        "chain": _manager_chain(person),
        "tasks": person.tasks.select_related("project", "state").exclude(state__category="done")[:15],
        "projects": person.projects.all()[:10],
        "can_edit": has_cap(request.user, "user.edit", person),
        "all_caps": Capability.objects.all(),
        "overrides": person.cap_overrides.select_related("capability"),
    }
    context.update(_work_for_day(person, request.GET.get("day")))
    return render(request, "accounts/user_detail.html", context)


WORK_DAY_MAX_ITEMS = 400


def _work_for_day(person, day_param):
    """What a person did on one day: pages they opened, project activity and audit-logged
    actions, grouped by project, plus the time they spent on Planwise that day."""
    from apps.audit.models import AuditLog
    from apps.projects.models import Activity

    today = timezone.localdate()
    day = parse_date(day_param or "") or today
    day = min(day, today)
    start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()))
    end = start + timezone.timedelta(days=1)

    items = []  # (when, project or None, kind, text, url)
    for v in PageVisit.objects.filter(user=person, visited_at__gte=start, visited_at__lt=end).select_related("project")[:WORK_DAY_MAX_ITEMS]:
        items.append((v.visited_at, v.project, "Opened", v.title or v.path, v.path))
    for a in Activity.objects.filter(actor=person, created_at__gte=start, created_at__lt=end).select_related("project")[:WORK_DAY_MAX_ITEMS]:
        items.append((a.created_at, a.project, "Did", a.verb[:1].upper() + a.verb[1:], ""))
    # project changes already appear above through Activity, so skip them here to avoid doubles
    audits = AuditLog.objects.filter(actor=person, created_at__gte=start, created_at__lt=end).exclude(module="projects")
    for a in audits[:WORK_DAY_MAX_ITEMS]:
        text = f"{a.get_action_display()} · {a.get_module_display()}"
        if a.object_repr:
            text += f" · {a.object_repr}"
        items.append((a.created_at, None, "Did", text, ""))
    items.sort(key=lambda i: i[0])

    groups = {}
    for when, project, kind, text, url in items:
        key = project.pk if project else None
        group = groups.setdefault(key, {"project": project, "entries": []})
        last = group["entries"][-1] if group["entries"] else None
        if last and last["kind"] == kind and last["text"] == text:
            last["count"] += 1  # same page / action again: show once with a count
            continue
        group["entries"].append({"when": when, "kind": kind, "text": text, "url": url, "count": 1})
    ordered = sorted(groups.values(), key=lambda g: (g["project"] is None, g["entries"][0]["when"]))

    seconds = (
        UserDayActivity.objects.filter(user=person, date=day).values_list("active_seconds", flat=True).first() or 0
    )
    return {
        "work_actions": action_counts([person.pk], start, end)[person.pk],
        "work_day": day,
        "work_is_today": day == today,
        "work_groups": ordered,
        "work_seconds": seconds,
        "work_prev": day - timezone.timedelta(days=1),
        "work_next": day + timezone.timedelta(days=1) if day < today else None,
    }


def _manager_chain(user):
    chain, seen, node = [], set(), user.manager
    while node and node.pk not in seen:
        seen.add(node.pk)
        chain.append(node)
        node = node.manager
    return chain


def _describe_user_changes(form):
    """"role: Member -> BDA; is_active: True -> False" for the audit log. Passwords are never written down."""
    parts = []
    for name in form.changed_data:
        if name == "password":
            parts.append("password changed")
            continue
        field = form.fields[name]
        old, new = form.initial.get(name), form.cleaned_data.get(name)
        related = getattr(getattr(field, "queryset", None), "model", None)
        if related is not None and old not in (None, ""):
            old = related.objects.filter(pk=old).first() or old
        parts.append(f"{name}: {str(old if old not in (None, '') else '-')[:60]} -> {str(new if new not in (None, '') else '-')[:60]}")
    return "; ".join(parts)[:4000]


@login_required
def user_form(request, pk=None):
    person = get_object_or_404(User, pk=pk) if pk else None
    require(request.user, "user.edit" if pk else "user.create", person)
    form = UserForm(request.POST or None, instance=person, editor=request.user)
    if request.method == "POST" and form.is_valid():
        was_create = pk is None
        changes = "" if was_create else _describe_user_changes(form)
        was_active = form.initial.get("is_active") if person else None  # not person.is_active: validation already applied the new value
        obj = form.save()
        invalidate(obj)
        audit_log(request.user, "create" if was_create else "edit", "users", target=obj.username, details=changes)
        if was_active is not None and obj.is_active != was_active:
            audit_log(request.user, "activate" if obj.is_active else "deactivate", "users", target=obj.username,
                      details="from the Edit form")
        messages.success(request, f"User {obj.username} saved.")
        return redirect("accounts:user_detail", obj.pk)
    return render(request, "accounts/user_form.html", {
        "form": form, "person": person, "can_mfa": has_cap(request.user, "security.manage"),
        "mfa_mode": mfa.mode()})


@login_required
@require_POST
def user_toggle_active(request, pk):
    person = get_object_or_404(User, pk=pk)
    require(request.user, "user.edit", person)
    person.is_active = not person.is_active
    person.save(update_fields=["is_active"])
    invalidate(person)
    audit_log(request.user, "activate" if person.is_active else "deactivate", "users", target=person.username)
    return redirect("accounts:user_detail", person.pk)


@login_required
@require_POST
def user_delete(request, pk):
    person = get_object_or_404(User, pk=pk)
    require(request.user, "user.delete")
    if person.pk == request.user.pk:
        messages.error(request, "You can't delete your own account.")
        return redirect("accounts:user_detail", person.pk)
    username = person.username
    person.delete()
    invalidate()
    audit_log(request.user, "delete", "users", target=username)
    messages.success(request, f"User {username} deleted.")
    return redirect("accounts:user_list")


@login_required
@require_POST
def user_cap_override(request, pk):
    person = get_object_or_404(User, pk=pk)
    require(request.user, "role.manage")
    code = request.POST.get("capability")
    scope = request.POST.get("scope")
    capability = get_object_or_404(Capability, pk=code)
    if scope == "remove":
        UserCapability.objects.filter(user=person, capability=capability).delete()
    else:
        UserCapability.objects.update_or_create(
            user=person,
            capability=capability,
            defaults={"scope": "own" if scope == "deny" else scope, "deny": scope == "deny"},
        )
    invalidate(person)
    audit_log(
        request.user, "permission_change", "permissions",
        target=f"{person.username}: {capability.code}={scope}",
    )
    return redirect("accounts:user_detail", person.pk)


def _permission_rows(person):
    """One row per capability for the checkbox page. `checked` = the person can do it right now.
    kind: role (comes from the role) | added (extra for this person) | revoked (taken away for this
    person) | limits (an older override that narrows the role) | none. A scope picker is only
    offered where a personal setting exists or is possible: extra grants and role-widening."""
    role = role_scopes(person)
    effective = effective_caps(person, use_cache=False)
    overrides = {uc.capability_id: uc for uc in person.cap_overrides.all()}
    rows = []
    for cap in Capability.objects.all():
        ov = overrides.get(cap.pk)
        from_role = role.get(cap.code)
        if ov and ov.deny:
            kind = "revoked"
        elif ov and from_role is None:
            kind = "added"
        elif ov and SCOPE_RANK[ov.scope] > SCOPE_RANK[from_role]:
            kind = "added"
        elif ov and SCOPE_RANK[ov.scope] < SCOPE_RANK[from_role]:
            kind = "limits"
        else:
            kind = "role" if from_role else "none"
        personal = ov is not None and not ov.deny
        scope_value = ov.scope if personal else ("all" if from_role is None else "")
        # a role-given permission can be widened for this person (own -> team -> all); never offer a
        # scope below the role's, which would only restrict it (unless an older override already sits there)
        floor = SCOPE_RANK.get(from_role, 0)
        options = [s for s in ("own", "team", "all") if SCOPE_RANK[s] > floor or s == scope_value]
        rows.append({
            "cap": cap, "category": cap.category, "role": from_role, "kind": kind,
            "checked": bool(effective.get(cap.code)), "effective": effective.get(cap.code),
            "show_scope": bool(options),  # nothing wider to offer when the role already gives "all"
            "scope_value": scope_value,
            "scope_options": options,
        })
    return rows


@login_required
def user_permissions(request, pk):
    """Tick the permissions this person should have, on top of their role. A ticked box the role
    doesn't grant is an extra for them alone; unticking one the role does grant takes it away from
    them alone (a deny). Their access is always the role's permissions adjusted by what is set here."""
    require(request.user, "role.manage")
    person = get_object_or_404(User, pk=pk)

    if request.method == "POST":
        role = role_scopes(person)
        existing = {uc.capability_id: uc for uc in person.cap_overrides.all()}
        seen = {int(v) for v in request.POST.getlist("seen") if v.isdigit()}
        changed, skipped = [], []
        with transaction.atomic():
            for cap in Capability.objects.filter(pk__in=seen):
                ov = existing.get(cap.pk)
                current = "" if ov is None else ("deny" if ov.deny else ov.scope)
                from_role = role.get(cap.code)
                picked = request.POST.get(f"scope_{cap.pk}")  # None = no picker was shown for this row
                if f"has_{cap.pk}" in request.POST:
                    if from_role is None:
                        # a scope picked on the form; otherwise keep an existing grant, or start at "all"
                        want = picked if picked in SCOPE_RANK else (current if current in SCOPE_RANK else "all")
                    elif picked is None:
                        want = "" if current == "deny" else current
                    elif picked == current:
                        want = current  # the picker was left as it was
                    elif picked in SCOPE_RANK and picked != from_role:
                        want = picked
                    else:
                        want = ""  # "Role default", or the role's own scope: nothing personal needed
                else:
                    want = "deny" if from_role else ""
                if want == current:
                    continue
                if want in SCOPE_RANK and from_role and SCOPE_RANK[want] < SCOPE_RANK[from_role]:
                    skipped.append(cap.code)  # a lower scope than the role's would only restrict it
                    continue
                if want == "":
                    ov.delete()
                else:
                    UserCapability.objects.update_or_create(
                        user=person, capability=cap,
                        defaults={"scope": "own" if want == "deny" else want, "deny": want == "deny"},
                    )
                changed.append(cap.code)
                audit_log(
                    request.user, "permission_change", "permissions", target=f"{person.username}: {cap.code}",
                    details=f"personal permission {current or 'none'} -> {want or 'none'}",
                )
        invalidate(person)
        if changed:
            messages.success(request, f"Saved {len(changed)} permission change{'s' if len(changed) != 1 else ''} for "
                                      f"{person.display_name}. They apply on their next page load.")
        if skipped:
            messages.warning(request, "Not changed, because a scope below the role's would only restrict it: "
                                      + ", ".join(skipped) + ".")
        if not changed and not skipped:
            messages.info(request, "No changes to save.")
        return redirect("accounts:user_permissions", person.pk)

    rows = _permission_rows(person)
    count = lambda kind: sum(1 for r in rows if r["kind"] == kind)  # noqa: E731
    return render(request, "accounts/user_permissions.html", {
        "person": person, "rows": rows,
        "stats": {
            "role": sum(1 for r in rows if r["role"]), "added": count("added"),
            "revoked": count("revoked"), "limits": count("limits"),
            "effective": sum(1 for r in rows if r["checked"]),
        },
    })


@login_required
def org_chart(request):
    require(request.user, "user.view")
    people = visible_users(request.user).all()
    ids = {u.pk for u in people}
    nodes = [
        {
            "id": u.pk,
            "name": u.display_name,
            "title": u.title or (u.role.name if u.role_id else ""),
            "role": u.role.name if u.role_id else "",
            "color": u.role.color if u.role_id else u.color,
            "team": u.team.name if u.team_id else "",
            "initials": u.initials,
            "parent": u.manager_id if u.manager_id in ids else None,
            "url": f"/users/{u.pk}/",
        }
        for u in people
    ]
    teams = [
        {"id": t.pk, "name": t.name, "parent": t.parent_id, "color": t.color, "lead": t.lead.display_name if t.lead_id else ""}
        for t in Team.objects.select_related("lead")
    ]
    roles = [
        {"id": r.pk, "name": r.name, "parent": r.parent_id, "color": r.color, "rank": r.rank,
         "users": r.users.count(), "url": f"/roles/{r.pk}/"}
        for r in Role.objects.annotate(n=Count("users"))
    ]
    import json

    return render(
        request,
        "accounts/org_chart.html",
        {"nodes": json.dumps(nodes), "teams": json.dumps(teams), "roles": json.dumps(roles)},
    )


# --------------------------------------------------------------------------- roles / capabilities
@login_required
def role_list(request):
    require(request.user, "role.manage")
    roles = (
        Role.objects.annotate(n_users=Count("users", distinct=True))
        .select_related("parent")
        .prefetch_related("role_caps__capability")
    )

    q = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    visible = roles
    if q:
        visible = [r for r in visible if q.lower() in r.name.lower() or q.lower() in r.description.lower()]
    if kind == "system":
        visible = [r for r in visible if r.is_system]
    elif kind == "custom":
        visible = [r for r in visible if not r.is_system]

    rows = []
    for role in sorted(visible, key=lambda r: (r.rank, r.name)):
        label, badge_class = _permission_summary(role)
        icon_bg, icon_color = ROLE_ICON_COLORS.get(role.slug, ("#f1f5f9", "#64748b"))
        rows.append({
            "role": role,
            "icon_key": ROLE_ICON_KEYS.get(role.slug, "user"),
            "icon_bg": icon_bg,
            "icon_color": icon_color,
            "badge_label": label,
            "badge_class": badge_class,
        })

    return render(
        request,
        "accounts/role_list.html",
        {"rows": rows, "capabilities": Capability.objects.all(), "form": RoleForm(), "q": q, "kind": kind},
    )


@login_required
def role_detail(request, pk):
    require(request.user, "role.manage")
    role = get_object_or_404(Role, pk=pk)
    grants = {rc.capability_id: rc.scope for rc in role.role_caps.all()}
    inherited = {}
    for ancestor in role.ancestors()[1:]:
        for rc in ancestor.role_caps.select_related("capability"):
            inherited.setdefault(rc.capability_id, (ancestor, rc.scope))
    by_category = {}
    for cap in Capability.objects.all():
        by_category.setdefault(cap.category, []).append(
            {"cap": cap, "scope": grants.get(cap.pk), "inherited": inherited.get(cap.pk)}
        )
    return render(
        request,
        "accounts/role_detail.html",
        {
            "role": role,
            "by_category": by_category,
            "scopes": SCOPES,
            "form": RoleForm(instance=role),
            "users": role.users.all(),
        },
    )


@login_required
@require_POST
def role_save(request, pk=None):
    require(request.user, "role.manage")
    role = get_object_or_404(Role, pk=pk) if pk else None
    form = RoleForm(request.POST, instance=role)
    if form.is_valid():
        obj = form.save()
        invalidate()
        messages.success(request, f"Role {obj.name} saved.")
        return redirect("accounts:role_detail", obj.pk)
    messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
    return redirect("accounts:role_list")


@login_required
@require_POST
def role_delete(request, pk):
    require(request.user, "role.manage")
    role = get_object_or_404(Role, pk=pk)
    name = role.name
    role.delete()
    invalidate()
    messages.success(request, f"Role {name} deleted.")
    return redirect("accounts:role_list")


@login_required
@require_POST
def role_grant(request, pk):
    """Toggle/scope a capability on a role. Called by fetch() from the matrix UI."""
    require(request.user, "role.manage")
    role = get_object_or_404(Role, pk=pk)
    import json

    payload = json.loads(request.body or "{}")
    capability = get_object_or_404(Capability, pk=payload.get("capability"))
    scope = payload.get("scope")
    if scope in (None, "", "none"):
        RoleCapability.objects.filter(role=role, capability=capability).delete()
    else:
        RoleCapability.objects.update_or_create(role=role, capability=capability, defaults={"scope": scope})
    invalidate()
    audit_log(request.user, "permission_change", "permissions", target=f"Role {role.name}: {capability.code}={scope}")
    return JsonResponse({"ok": True, "role": role.pk, "capability": capability.pk, "scope": scope})


@login_required
def capability_list(request):
    require(request.user, "role.manage")
    form = CapabilityForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        invalidate()
        messages.success(request, "Capability added.")
        return redirect("accounts:capability_list")
    caps = Capability.objects.annotate(n_roles=Count("role_caps"))
    return render(request, "accounts/capability_list.html", {"caps": caps, "form": form})


# --------------------------------------------------------------------------- teams
@login_required
def team_list(request):
    require(request.user, "team.manage")
    teams = Team.objects.select_related("parent", "lead").annotate(n=Count("members"))
    people = User.objects.filter(is_active=True).select_related("team").order_by("first_name", "username")
    return render(request, "accounts/team_list.html", {"teams": teams, "form": TeamForm(), "people": people})


@login_required
@require_POST
def team_save(request, pk=None):
    require(request.user, "team.manage")
    team = get_object_or_404(Team, pk=pk) if pk else None
    form = TeamForm(request.POST, instance=team)
    if form.is_valid():
        form.save()
        messages.success(request, "Team saved.")
    else:
        messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
    return redirect("accounts:team_list")


@login_required
@require_POST
def team_delete(request, pk):
    require(request.user, "team.manage")
    get_object_or_404(Team, pk=pk).delete()
    messages.success(request, "Team deleted.")
    return redirect("accounts:team_list")


@login_required
@require_POST
def team_add_members(request, pk):
    require(request.user, "team.manage")
    team = get_object_or_404(Team, pk=pk)
    ids = request.POST.getlist("user_ids")
    if ids:
        count = User.objects.filter(pk__in=ids).update(team=team)
        messages.success(request, f"Added {count} member(s) to {team.name}.")
    return redirect("accounts:team_list")
