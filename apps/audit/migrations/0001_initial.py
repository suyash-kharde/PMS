# Generated manually to match the project's existing migration style.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("accounts", "0004_user_last_active"),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("create", "Create"),
                            ("edit", "Edit"),
                            ("delete", "Delete"),
                            ("login", "Login"),
                            ("logout", "Logout"),
                            ("assign_vm", "Assign VM"),
                            ("unassign_vm", "Unassign VM"),
                            ("permission_change", "Permission Change"),
                            ("activate", "Activate"),
                            ("deactivate", "Deactivate"),
                        ],
                        max_length=40,
                    ),
                ),
                (
                    "module",
                    models.CharField(
                        choices=[
                            ("auth", "Login/Logout"),
                            ("users", "Users"),
                            ("permissions", "Permissions"),
                            ("projects", "Projects"),
                            ("vms", "VMs"),
                        ],
                        max_length=40,
                    ),
                ),
                (
                    "object_repr",
                    models.CharField(blank=True, help_text="Human-readable record/entity affected", max_length=255),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("success", "Success"), ("failed", "Failed")], default="success", max_length=10
                    ),
                ),
                ("details", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["actor", "created_at"], name="audit_actor_created_idx"),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["module"], name="audit_module_idx"),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["action"], name="audit_action_idx"),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["status"], name="audit_status_idx"),
        ),
    ]
