"""Who may see or change what (spec §8), mapped onto Planwise's existing people data:

  Admin              superuser or the "admin" role
  Reporting Manager  anyone with people reporting to them (User.manager), or the "reporting-manager" role;
                     "their team" = everyone below them in the reporting line
  Project Lead       the owner of a project; sees hours for their own projects only
  Employee           everyone else; their own timesheet only
"""
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from apps.accounts.models import SCOPE_ALL, SCOPE_TEAM, User
from apps.accounts.permissions import has_cap, scope_of
from apps.projects.models import Project

from . import rules

ADMIN_ROLE = "admin"
RM_ROLE = "reporting-manager"


def is_admin(user):
    return bool(user and user.is_authenticated and user.is_site_admin)  # Administrator or Devadmin


def team_ids(user):
    """Everyone below this person in the reporting line (not including them)."""
    return user.subordinate_ids(include_self=False)


def is_reporting_manager(user):
    if not (user and user.is_authenticated):
        return False
    return getattr(user.role, "slug", "") == RM_ROLE or User.objects.filter(manager_id=user.pk).exists()


def manages(user, target):
    return target.pk != user.pk and target.pk in team_ids(user)


def led_project_ids(user):
    return set(Project.objects.filter(owner_id=user.pk).values_list("pk", flat=True))


VIEW_CAP = "timesheet.view"


def view_scope(user):
    """The "Timesheet management view" permission (Permissions page): "all", "team" or None. VIEW only —
    editing, overrides and unlocking still follow admin / reporting-manager rules below."""
    return scope_of(user, VIEW_CAP) if user and user.is_authenticated else None


def sees_everyone(user):
    return is_admin(user) or view_scope(user) == SCOPE_ALL


def can_see_team(user):
    """Opens the People / Attendance / Reports management tabs."""
    return sees_everyone(user) or is_reporting_manager(user) or view_scope(user) == SCOPE_TEAM


def view_ids(user):
    """Whose timesheets this person may see, as ids (their own included); None = everyone."""
    if sees_everyone(user):
        return None
    ids = team_ids(user) | {user.pk}
    if view_scope(user) == SCOPE_TEAM and user.team_id:  # plus the people in their team (and sub-teams)
        ids |= set(User.objects.filter(team_id__in=user.team.descendant_ids()).values_list("pk", flat=True))
    return ids


def can_view_timesheet(user, target):
    if target.pk == user.pk or manages(user, target):
        return True
    ids = view_ids(user)
    return ids is None or target.pk in ids


def staff_accounts():
    """Active internal people. Client-side logins are left out: the Customer role, or a customer link with
    no role at all (see User.is_client_login). Staff merely linked to a customer record, such as a BDA on
    that customer's account, keep a timesheet like everyone else."""
    return User.objects.filter(is_active=True).exclude(
        Q(role__slug=User.CLIENT_ROLE_SLUG) | Q(role__isnull=True, customer__isnull=False)
    )


def viewable_people(user):
    """People whose full timesheet this person may open (themselves first)."""
    qs = staff_accounts()
    ids = view_ids(user)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    return qs.select_related("role").order_by("first_name", "username")


def has_unlock(target, day):
    from .models import TimesheetUnlock

    return TimesheetUnlock.objects.filter(
        user=target, month=day.replace(day=1), active=True
    ).exists()


EDIT_CLOSED = "timesheet.edit_closed"


def can_edit_day(user, target, day, today):
    """Admin (Administrator / Devadmin): anyone, any date.
    While the month is open: the person themselves and their reporting manager.
    Once the month has closed: only someone holding the "Edit timesheets of closed months" permission (set per
    role and per person), for the people whose timesheets they can see, or the person themselves / their
    reporting manager while an admin has unlocked that month for them.
    A Project Lead gets no extra edit rights."""
    if is_admin(user):
        return True
    own = target.pk == user.pk or manages(user, target)
    if not rules.is_locked(day, today):
        return own
    if own and has_unlock(target, day):
        return True
    return has_cap(user, EDIT_CLOSED) and can_view_timesheet(user, target)


HOLIDAYS_MANAGE = "timesheet.holidays.manage"


def can_manage_holidays(user):
    """Add, edit and delete holidays and extra working days: the "Manage holidays & working days" permission,
    set per role and per person (Administrator and Devadmin hold it to start with)."""
    return has_cap(user, HOLIDAYS_MANAGE)


def can_override_attendance(user, target):
    """Employees can never override their own attendance."""
    if target.pk == user.pk and not is_admin(user):
        return False
    return is_admin(user) or manages(user, target)


def can_set_remainder(user, target):
    return is_admin(user) or manages(user, target)


def can_set_main_projects(user, target):
    """The person themselves, their reporting manager, or an admin."""
    return target.pk == user.pk or is_admin(user) or manages(user, target)


def can_manage_profile(user):
    """Joining date / last working day / unlocks: admin only."""
    return is_admin(user)


def require(ok):
    if not ok:
        raise PermissionDenied("You don't have access to this timesheet action.")
