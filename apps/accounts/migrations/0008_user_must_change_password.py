"""Every existing account (and every new one) is asked to change its password once."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_default_member_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="must_change_password",
            field=models.BooleanField(
                default=True,
                help_text="Asked to set their own password once, at their next sign-in. Cleared when they change it.",
            ),
        ),
    ]
