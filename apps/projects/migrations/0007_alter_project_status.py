from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0006_alter_projectdocument_kind'),
    ]

    operations = [
        migrations.AlterField(
            model_name='project',
            name='status',
            field=models.CharField(choices=[('lead', 'Lead'), ('proposal', 'Proposal Submitted'), ('active', 'Active'), ('under_amc', 'Under AMC'), ('completed', 'Completed'), ('planning', 'Planning'), ('on_hold', 'On hold'), ('cancelled', 'Cancelled')], default='planning', max_length=12),
        ),
    ]
