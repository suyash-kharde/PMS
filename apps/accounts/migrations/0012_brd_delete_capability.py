from django.db import migrations

# Permission to delete a project's whole BRD (all versions). Only roles that can already manage roles
# (administrators) get it; an admin can tick it for any other role or person on the permissions pages.


def add_brd_delete(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    RoleCapability = apps.get_model("accounts", "RoleCapability")
    cap, _ = Capability.objects.get_or_create(
        code="brd.delete",
        defaults={"label": "Delete the BRD (every version, cannot be undone)", "category": "brd"},
    )
    for rc in RoleCapability.objects.filter(capability__code="role.manage").select_related("role"):
        RoleCapability.objects.get_or_create(role=rc.role, capability=cap, defaults={"scope": "all"})


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_vm_manage_capability"),
    ]

    operations = [
        migrations.RunPython(add_brd_delete, migrations.RunPython.noop),
    ]
