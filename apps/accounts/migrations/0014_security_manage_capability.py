from django.db import migrations

# "Security settings" (the auto sign-out time page). Only the Devadmin role gets it to start with;
# any other role is granted it on the Roles page.
CODE, LABEL, CATEGORY = "security.manage", "Security settings: set the auto sign-out time", "admin"


def add_capability(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    Role = apps.get_model("accounts", "Role")
    RoleCapability = apps.get_model("accounts", "RoleCapability")
    cap, _ = Capability.objects.get_or_create(code=CODE, defaults={"label": LABEL, "category": CATEGORY})
    for role in Role.objects.filter(slug="devadmin"):
        RoleCapability.objects.get_or_create(role=role, capability=cap, defaults={"scope": "all"})


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0013_sitesetting'),
    ]

    operations = [
        migrations.RunPython(add_capability, migrations.RunPython.noop),
    ]
