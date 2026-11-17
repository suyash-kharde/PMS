from django.db import migrations, models

# every project still on a removed status is moved to the closest remaining one
LEGACY_TO_NEW = {"planning": "lead", "on_hold": "active", "cancelled": "completed"}


def move_legacy_statuses(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    for old, new in LEGACY_TO_NEW.items():
        Project.objects.filter(status=old).update(status=new)


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0008_project_po_number_project_po_document'),
    ]

    operations = [
        migrations.RunPython(move_legacy_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='project',
            name='status',
            field=models.CharField(choices=[('lead', 'Lead'), ('proposal', 'Proposal Submitted'), ('active', 'Active'), ('under_amc', 'Under AMC'), ('completed', 'Completed')], default='lead', max_length=12),
        ),
    ]
