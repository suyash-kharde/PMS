from django.db import migrations


def ensure_vm_capability(apps, schema_editor):
    """The VMs page, its menu entry and now its dashboard tile are all gated by vm.manage. Make sure the
    permission exists so it shows on the role page; nobody is granted it here, and a row that already
    exists (with its role grants) is left exactly as it is."""
    Capability = apps.get_model("accounts", "Capability")
    Capability.objects.get_or_create(
        code="vm.manage",
        defaults={"label": "Manage VMs (VMs page, project VMs tab, dashboard VMs tile)", "category": "admin"},
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_timesheet_tab_capabilities"),
    ]

    operations = [
        migrations.RunPython(ensure_vm_capability, migrations.RunPython.noop),
    ]
