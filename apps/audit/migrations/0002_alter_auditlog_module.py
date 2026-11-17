# Generated manually to match the project's existing migration style.
# No real schema change — `choices` is Django-side metadata only, this just keeps
# migration state in sync with models.py (entrypoint.sh's makemigrations --check would
# otherwise flag a mismatch on the next deploy).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditlog",
            name="module",
            field=models.CharField(
                choices=[
                    ("auth", "Login/Logout"),
                    ("users", "Users"),
                    ("permissions", "Permissions"),
                    ("projects", "Projects"),
                    ("vms", "VMs"),
                    ("org", "Organisation"),
                    ("tickets", "Tickets"),
                    ("sprints", "Sprints"),
                    ("brd", "Timeline / BRD"),
                    ("ai", "AI Models"),
                    ("bulk_import", "Bulk Import"),
                    ("chat", "Chat"),
                    ("mcp", "Claude / MCP"),
                    ("devlog", "Dev Log"),
                    ("design", "Design"),
                ],
                max_length=40,
            ),
        ),
    ]
