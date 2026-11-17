import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("projects", "0001_initial"),
        ("sprints", "0001_initial"),
        ("tickets", "0001_initial"),
        ("mcp", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DevUpdate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("headline", models.CharField(help_text="What was done", max_length=240)),
                ("details", models.TextField(blank=True, help_text="Longer description (markdown)")),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("feature", "Feature"),
                            ("bugfix", "Bug fix"),
                            ("refactor", "Refactor"),
                            ("test", "Tests"),
                            ("docs", "Docs"),
                            ("chore", "Chore"),
                            ("research", "Research"),
                            ("other", "Other"),
                        ],
                        default="feature",
                        max_length=12,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("in_progress", "In progress"),
                            ("completed", "Completed"),
                            ("blocked", "Blocked"),
                            ("needs_review", "Needs review"),
                        ],
                        default="in_progress",
                        max_length=12,
                    ),
                ),
                (
                    "milestone",
                    models.CharField(
                        blank=True,
                        help_text="Free-text milestone this work is progressing toward",
                        max_length=200,
                    ),
                ),
                (
                    "milestone_done",
                    models.BooleanField(default=False, help_text="This update completes that milestone"),
                ),
                ("files_changed", models.JSONField(blank=True, default=list, help_text="List of file paths touched")),
                ("branch", models.CharField(blank=True, max_length=200)),
                ("commit", models.CharField(blank=True, max_length=120)),
                ("pr_url", models.URLField(blank=True)),
                (
                    "source",
                    models.CharField(
                        choices=[("mcp", "Claude Code (MCP)"), ("web", "Web UI"), ("admin", "Admin")],
                        default="web",
                        max_length=8,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dev_updates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dev_updates",
                        to="projects.project",
                    ),
                ),
                (
                    "requirement",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dev_updates",
                        to="sprints.requirement",
                    ),
                ),
                (
                    "sprint",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dev_updates",
                        to="sprints.sprint",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dev_updates",
                        to="projects.task",
                    ),
                ),
                (
                    "ticket",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dev_updates",
                        to="tickets.ticket",
                    ),
                ),
                (
                    "via_token",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dev_updates",
                        to="mcp.projectmcptoken",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="devupdate",
            index=models.Index(fields=["project", "status"], name="devlog_project_status_idx"),
        ),
        migrations.AddIndex(
            model_name="devupdate",
            index=models.Index(fields=["project", "milestone"], name="devlog_project_milestone_idx"),
        ),
    ]
