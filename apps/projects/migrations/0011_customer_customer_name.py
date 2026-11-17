from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0010_customer_created_by'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='customer_name',
            field=models.CharField(blank=True, max_length=160),
        ),
    ]
