import apps.projects.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0007_alter_project_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='project',
            name='po_number',
            field=models.CharField(blank=True, max_length=100, verbose_name='PO number'),
        ),
        migrations.AddField(
            model_name='project',
            name='po_document',
            field=models.FileField(blank=True, upload_to=apps.projects.models.po_upload_to, verbose_name='PO document'),
        ),
    ]
