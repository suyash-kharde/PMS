from django.db import migrations

# On/off permissions for the six tabs of the Timesheet page. Every existing role is given each one, so
# nobody loses a tab when this ships; an admin then unticks it for a role or person. (People and
# Attendance still also need the usual reporting-manager / timesheet-management access.)
TAB_CAPS = [
    ("timesheet.tab.week", "Timesheet tab: my weekly timesheet", "timesheet"),
    ("timesheet.tab.people", "Timesheet tab: People", "timesheet"),
    ("timesheet.tab.calendar", "Timesheet tab: Calendar", "timesheet"),
    ("timesheet.tab.holidays", "Timesheet tab: Holidays", "timesheet"),
    ("timesheet.tab.attendance", "Timesheet tab: Attendance", "timesheet"),
    ("timesheet.tab.reports", "Timesheet tab: Reports", "timesheet"),
]


def add_tab_caps(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    Role = apps.get_model("accounts", "Role")
    RoleCapability = apps.get_model("accounts", "RoleCapability")
    for code, label, category in TAB_CAPS:
        cap, _ = Capability.objects.get_or_create(code=code, defaults={"label": label, "category": category})
        for role in Role.objects.all():
            RoleCapability.objects.get_or_create(role=role, capability=cap, defaults={"scope": "all"})


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_menu_capabilities"),
    ]

    operations = [
        migrations.RunPython(add_tab_caps, migrations.RunPython.noop),
    ]
