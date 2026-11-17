from django.db import migrations

# "Edit timesheets of closed months": who may change hours once a month has ended. Administrator and Devadmin
# hold it to start with (they can edit any month anyway); any other role or person is granted it on the Roles /
# person permissions pages.
CODE = "timesheet.edit_closed"
LABEL = "Timesheet: edit hours after the month has closed"


def add_capability(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    Role = apps.get_model("accounts", "Role")
    RoleCapability = apps.get_model("accounts", "RoleCapability")
    cap, _ = Capability.objects.get_or_create(code=CODE, defaults={"label": LABEL, "category": "timesheet"})
    for role in Role.objects.filter(slug__in=("admin", "devadmin")):
        RoleCapability.objects.get_or_create(role=role, capability=cap, defaults={"scope": "all"})


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0016_holidays_manage_capability'),
    ]

    operations = [
        migrations.RunPython(add_capability, migrations.RunPython.noop),
    ]
