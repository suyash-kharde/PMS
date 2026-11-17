"""Mint, list and revoke per-project Claude Code / MCP links from the command line.

    python manage.py mcp_token create --project PLAT --user aisha --ticket-write --sprint-write
    python manage.py mcp_token list --project PLAT
    python manage.py mcp_token revoke 4
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.projects.models import Project

from ...models import ProjectMCPToken

User = get_user_model()


class Command(BaseCommand):
    help = "Manage per-project Claude Code / MCP access links."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)

        c = sub.add_parser("create", help="mint a new link (prints the URL once)")
        c.add_argument("--project", required=True, help="project key, e.g. PLAT")
        c.add_argument("--user", required=True, help="username the link acts as")
        c.add_argument("--name", default="Claude Code")
        c.add_argument("--ticket-write", action="store_true")
        c.add_argument("--sprint-write", action="store_true")
        c.add_argument(
            "--base-url",
            default="https://localhost",
            help="site origin for the printed link (default https://localhost)",
        )

        li = sub.add_parser("list", help="list links")
        li.add_argument("--project", help="filter by project key")

        r = sub.add_parser("revoke", help="revoke a link by id")
        r.add_argument("id", type=int)

    def handle(self, *args, **opts):
        getattr(self, f"_{opts['action']}")(opts)

    def _create(self, opts):
        try:
            project = Project.objects.get(key=opts["project"])
        except Project.DoesNotExist:
            raise CommandError(f"No project with key {opts['project']!r}.")
        try:
            user = User.objects.get(username=opts["user"])
        except User.DoesNotExist:
            raise CommandError(f"No user {opts['user']!r}.")

        raw, token_hash, prefix = ProjectMCPToken.generate()
        ProjectMCPToken.objects.create(
            project=project,
            created_by=user,
            name=opts["name"],
            token_hash=token_hash,
            token_prefix=prefix,
            allow_ticket_write=opts["ticket_write"],
            allow_sprint_write=opts["sprint_write"],
        )
        link = f"{opts['base_url'].rstrip('/')}/mcp/{raw}/"
        scopes = [s for s, on in (("ticket-write", opts["ticket_write"]), ("sprint-write", opts["sprint_write"])) if on]
        self.stdout.write(self.style.SUCCESS("MCP link created (shown once):"))
        self.stdout.write(f"  {link}")
        self.stdout.write(f"  project={project.key}  user={user.username}  scopes={scopes or ['read-only']}")
        self.stdout.write("")
        self.stdout.write("  claude mcp add --transport http "
                          f"planwise-{project.key.lower()} {link}")

    def _list(self, opts):
        qs = ProjectMCPToken.objects.select_related("project", "created_by").order_by("-created_at")
        if opts.get("project"):
            qs = qs.filter(project__key=opts["project"])
        if not qs:
            self.stdout.write("No links.")
            return
        for t in qs:
            scopes = "".join(s for s, on in (("T", t.allow_ticket_write), ("S", t.allow_sprint_write)) if on)
            user = t.created_by.username if t.created_by else "-"
            last_used = t.last_used_at.strftime("%Y-%m-%d %H:%M") if t.last_used_at else "never"
            self.stdout.write(
                f"#{t.pk:<4} {t.project.key:<8} {t.token_prefix}…  {t.status_label:<8} "
                f"scopes={scopes or '-':<3} user={user:<12} last_used={last_used}"
            )

    def _revoke(self, opts):
        try:
            token = ProjectMCPToken.objects.get(pk=opts["id"])
        except ProjectMCPToken.DoesNotExist:
            raise CommandError(f"No link #{opts['id']}.")
        token.revoke()
        self.stdout.write(self.style.SUCCESS(f"Revoked #{token.pk} ({token.project.key} · {token.name})."))
