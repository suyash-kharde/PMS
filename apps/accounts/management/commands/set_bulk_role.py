"""Give many people one role at once, leaving named people as they are.

Safe by default: it only PRINTS what it would change. Add --apply to do it.

    python manage.py set_bulk_role --role bda --except "Yash" "Suyash" "Abhishek Hengade" "Navneet" "Kirti"
    python manage.py set_bulk_role --role bda --except ... --apply

Skipped unless you say otherwise: superusers, administrators (anyone whose role can manage roles) and
client-side logins, so nobody loses admin access or a customer login by accident. Each name in --except
must match exactly one person (first name, last name, username or full name, ignoring case), otherwise
--apply refuses to run. Take a database backup first; every change is also written to the audit log
with the old role, so it can be reversed by hand.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from apps.accounts.models import Role, User
from apps.accounts.permissions import effective_caps, invalidate


def _full(user):
    return f"{user.first_name} {user.last_name}".strip()


def _matches(user, term):
    term = term.strip().lower()
    return term in {user.first_name.lower(), user.last_name.lower(), user.username.lower(), _full(user).lower()}


class Command(BaseCommand):
    help = "Set one role for everyone except the named people (dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--role", required=True, help="Role slug or name to give, e.g. bda")
        parser.add_argument("--except", dest="skip", nargs="*", default=[], help="People to leave unchanged")
        parser.add_argument("--apply", action="store_true", help="Actually write the changes")
        parser.add_argument("--include-admins", action="store_true", help="Also change superusers and administrators")
        parser.add_argument("--include-clients", action="store_true", help="Also change client-side logins")

    def handle(self, *args, **opts):
        role = Role.objects.filter(Q(slug__iexact=opts["role"]) | Q(name__iexact=opts["role"])).first()
        if not role:
            raise CommandError(f"No role called '{opts['role']}'.")

        people = list(User.objects.select_related("role").order_by("first_name", "username"))

        problems, protected = [], set()
        for term in opts["skip"]:
            hits = [u for u in people if _matches(u, term)]
            if len(hits) != 1:
                problems.append(f"'{term}' matches {len(hits)} people" + (": " + ", ".join(u.username for u in hits) if hits else ""))
            protected.update(u.pk for u in hits)

        rows, kept = [], []
        for user in people:
            reason = None
            if user.pk in protected:
                reason = "named in --except"
            elif user.role_id == role.pk:
                reason = "already has this role"
            elif not opts["include_admins"] and (user.is_superuser or "role.manage" in effective_caps(user, use_cache=False)):
                reason = "administrator (use --include-admins)"
            elif not opts["include_clients"] and user.is_client_login:
                reason = "client login (use --include-clients)"
            (kept if reason else rows).append((user, reason))

        self.stdout.write(f"Role to give: {role.name} ({role.slug}). People in total: {len(people)}.\n")
        self.stdout.write(self.style.MIGRATE_HEADING(f"Will change ({len(rows)}):"))
        for user, _ in rows:
            self.stdout.write(f"  {user.username:<22} {_full(user) or '-':<28} {getattr(user.role, 'name', 'no role')} -> {role.name}")
        self.stdout.write(self.style.MIGRATE_HEADING(f"\nLeft as they are ({len(kept)}):"))
        for user, reason in kept:
            self.stdout.write(f"  {user.username:<22} {_full(user) or '-':<28} {getattr(user.role, 'name', 'no role'):<18} {reason}")
        for problem in problems:
            self.stdout.write(self.style.WARNING(f"\nCHECK: {problem}"))

        if not opts["apply"]:
            self.stdout.write(self.style.NOTICE("\nDry run only. Nothing was changed. Add --apply to make these changes."))
            return
        if problems:
            raise CommandError("Not applied: fix the --except names above (each must match exactly one person).")

        from apps.audit.services import log

        with transaction.atomic():
            for user, _ in rows:
                old = getattr(user.role, "name", "no role")
                User.objects.filter(pk=user.pk).update(role=role)
                log(None, "edit", "users", target=user.username, details=f"Role changed from {old} to {role.name} (set_bulk_role)")
        invalidate()
        self.stdout.write(self.style.SUCCESS(f"\nDone: {len(rows)} people now have the role {role.name}."))
