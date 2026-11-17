# Generated manually to match the project's existing migration style.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("projects", "0006_alter_projectdocument_kind"),
    ]

    operations = [
        migrations.CreateModel(
            name="VM",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("host", models.CharField(help_text="IP address or hostname", max_length=255)),
                ("os", models.CharField(blank=True, max_length=80)),
                ("username", models.CharField(blank=True, max_length=120)),
                ("password", models.CharField(blank=True, help_text="Optional", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="vms_registered",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="ProjectVM",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("git_repo_url", models.URLField(blank=True)),
                ("git_branch", models.CharField(blank=True, default="main", max_length=120)),
                (
                    "folder_path",
                    models.CharField(
                        blank=True,
                        help_text="Where this project's code lives on the VM, e.g. /home/project-x",
                        max_length=255,
                    ),
                ),
                ("assigned_at", models.DateTimeField(auto_now_add=True)),
                (
                    "assigned_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vm_links",
                        to="projects.project",
                    ),
                ),
                (
                    "vm",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="project_links",
                        to="vms.vm",
                    ),
                ),
            ],
            options={"ordering": ["-assigned_at"]},
        ),
        migrations.AlterUniqueTogether(
            name="projectvm",
            unique_together={("project", "vm")},
        ),
    ]
