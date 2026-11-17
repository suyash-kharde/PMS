from django.db import migrations

# On/off permissions for three sidebar pages that used to be open to everybody. Every existing role is
# given each one, so nobody loses access when this ships; an admin then unticks it for a role or person.
MENU_CAPS = [
    ("mytasks.view", "My tasks page", "menu"),
    ("aiqueue.view", "AI queue page", "menu"),
    ("docs.view", "Documentation page", "menu"),
]


def add_menu_caps(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    Role = apps.get_model("accounts", "Role")
    RoleCapability = apps.get_model("accounts", "RoleCapability")
    for code, label, category in MENU_CAPS:
        cap, _ = Capability.objects.get_or_create(code=code, defaults={"label": label, "category": category})
        for role in Role.objects.all():
            RoleCapability.objects.get_or_create(role=role, capability=cap, defaults={"scope": "all"})


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_user_must_change_password'),
    ]

    operations = [
        migrations.RunPython(add_menu_caps, migrations.RunPython.noop),
    ]
