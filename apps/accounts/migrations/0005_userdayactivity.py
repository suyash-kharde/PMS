import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_user_last_active'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserDayActivity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('active_seconds', models.PositiveIntegerField(default=0)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='day_activity', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-date'],
                'constraints': [models.UniqueConstraint(fields=('user', 'date'), name='user_day_activity_unique')],
            },
        ),
    ]
