import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("projects", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectMCPToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(default="Claude Code", max_length=120)),
                ("token_hash", models.CharField(editable=False, max_length=64, unique=True)),
                ("token_prefix", models.CharField(editable=False, help_text="For display, e.g. pwmcp_a1b2", max_length=16)),
                ("allow_ticket_write", models.BooleanField(default=False, help_text="Allow commenting on tickets, changing status, raising bugs")),
                ("allow_sprint_write", models.BooleanField(default=False, help_text="Allow changing requirement status, recording test results, adding acceptance criteria")),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("last_used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        help_text="Permission checks for this link run as this user.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="mcp_tokens",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mcp_tokens",
                        to="projects.project",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
