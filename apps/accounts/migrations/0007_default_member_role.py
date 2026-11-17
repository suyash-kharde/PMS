from django.db import migrations


def give_member_role(apps, schema_editor):
    """Staff accounts that have no role get Member (client-side logins are left alone)."""
    Role = apps.get_model("accounts", "Role")
    User = apps.get_model("accounts", "User")
    member = Role.objects.filter(slug="member").first()
    if member:
        User.objects.filter(role__isnull=True, customer__isnull=True).update(role=member)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_pagevisit'),
    ]

    operations = [
        migrations.RunPython(give_member_role, migrations.RunPython.noop),
    ]
