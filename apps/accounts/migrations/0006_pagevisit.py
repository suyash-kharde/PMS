import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0005_userdayactivity'),
        ('projects', '0008_project_po_number_project_po_document'),
    ]

    operations = [
        migrations.CreateModel(
            name='PageVisit',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('path', models.CharField(max_length=300)),
                ('title', models.CharField(blank=True, max_length=200)),
                ('visited_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='projects.project')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='page_visits', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['visited_at'],
                'indexes': [models.Index(fields=['user', 'visited_at'], name='pagevisit_user_time_idx')],
            },
        ),
    ]
