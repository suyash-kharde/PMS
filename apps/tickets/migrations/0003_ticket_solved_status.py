from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tickets", "0002_ticket_team"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="handoff_status",
            field=models.CharField(
                blank=True, help_text="Where a Solved ticket goes next, e.g. Waiting for Sadhna Testing", max_length=200
            ),
        ),
        migrations.AlterField(
            model_name="ticket",
            name="status",
            field=models.CharField(
                choices=[
                    ("new", "New"), ("triaged", "Triaged"), ("accepted", "Accepted"), ("in_progress", "In progress"),
                    ("blocked", "Blocked"), ("solved", "Solved"), ("resolved", "Resolved"), ("closed", "Closed"),
                    ("rejected", "Rejected"),
                ],
                default="new", max_length=12,
            ),
        ),
    ]
