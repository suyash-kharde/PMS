"""The AI job queue table. New table only."""
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("projects", "0009_remove_legacy_project_statuses"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Job",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(max_length=30)),
                ("lane", models.CharField(help_text="Jobs in a lane share its worker slots (audio, ai, setup)", max_length=20)),
                ("label", models.CharField(help_text="What it is, for people: e.g. 'BRD update · DDMS'", max_length=200)),
                ("target_id", models.BigIntegerField(blank=True, help_text="The entry / plan run / workspace it works on", null=True)),
                ("params", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("done", "Done"), ("failed", "Failed"), ("cancelled", "Cancelled")], default="queued", max_length=10)),
                ("dedupe_key", models.CharField(blank=True, help_text="A second request with the same key joins the waiting job", max_length=80)),
                ("serial_key", models.CharField(blank=True, help_text="Jobs with the same key never run at the same time (one per project)", max_length=80)),
                ("size", models.FloatField(default=0, help_text="Audio file size in MB, used for the time estimate")),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("max_attempts", models.PositiveSmallIntegerField(default=3)),
                ("run_after", models.DateTimeField(default=django.utils.timezone.now, help_text="Not picked before this (retry back-off)")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("error", models.TextField(blank=True)),
                ("result_url", models.CharField(blank=True, max_length=300)),
                ("project", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="jobs", to="projects.project")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["status", "lane", "created_at"], name="job_pick_idx"),
                    models.Index(fields=["kind", "status", "finished_at"], name="job_timing_idx"),
                    models.Index(fields=["dedupe_key", "status"], name="job_dedupe_idx"),
                ],
            },
        ),
    ]
