"""Holidays per team. Existing holidays keep an empty team list, i.e. they still apply to everyone.
The UI/UX team is linked to follow the Pune team's holidays (skipped when either team is missing)."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def link_uiux_to_pune(apps, schema_editor):
    Team = apps.get_model("accounts", "Team")
    TeamHolidayLink = apps.get_model("timesheet", "TeamHolidayLink")
    uiux = Team.objects.filter(name__iexact="UI/UX Designer Team").first()
    pune = Team.objects.filter(name__iexact="pune team").first()
    if uiux and pune:
        TeamHolidayLink.objects.get_or_create(team=uiux, defaults={"follows": pune})


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_default_member_role"),
        ("timesheet", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="holiday",
            name="date",
            field=models.DateField(),
        ),
        migrations.AlterModelOptions(
            name="holiday",
            options={"ordering": ["date", "pk"]},
        ),
        migrations.AddIndex(
            model_name="holiday",
            index=models.Index(fields=["date"], name="ts_holiday_date_idx"),
        ),
        migrations.AddField(
            model_name="holiday",
            name="teams",
            field=models.ManyToManyField(blank=True, related_name="+", to="accounts.team"),
        ),
        migrations.CreateModel(
            name="TeamHolidayLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("follows", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="accounts.team")),
                ("team", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="accounts.team")),
            ],
        ),
        migrations.RunPython(link_uiux_to_pune, migrations.RunPython.noop),
    ]
