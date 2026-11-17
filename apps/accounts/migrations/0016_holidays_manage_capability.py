from django.db import migrations

# "Manage holidays & working days": who may add, edit and delete holidays and extra working days. Only the
# Administrator and Devadmin roles get it to start with (reporting managers no longer do); any other role or
# person is granted it on the Roles / person permissions pages.
CODE = "timesheet.holidays.manage"
LABEL = "Timesheet: add, edit and delete holidays and extra working days"


def add_capability(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    Role = apps.get_model("accounts", "Role")
    RoleCapability = apps.get_model("accounts", "RoleCapability")
    cap, _ = Capability.objects.get_or_create(code=CODE, defaults={"label": LABEL, "category": "timesheet"})
    for role in Role.objects.filter(slug__in=("admin", "devadmin")):
        RoleCapability.objects.get_or_create(role=role, capability=cap, defaults={"scope": "all"})


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_user_mfa'),
    ]

    operations = [
        migrations.RunPython(add_capability, migrations.RunPython.noop),
    ]
