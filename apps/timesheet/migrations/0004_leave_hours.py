"""The Leave row: leave hours per timesheet day. Additive; every existing day gets 0."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("timesheet", "0003_main_projects"),
    ]

    operations = [
        migrations.AddField(
            model_name="timesheetday",
            name="leave_hours",
            field=models.PositiveSmallIntegerField(
                default=0, help_text="Hours on leave that day (the Leave row). They count toward the 8-hour day."
            ),
        ),
    ]
