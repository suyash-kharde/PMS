from django.db import migrations, models


class Migration(migrations.Migration):
    """Adds only new columns to the user table (nothing existing is changed). Each has a database default, so
    older code that does not know the columns can still create users; `migrate accounts 0014` removes them."""

    dependencies = [
        ('accounts', '0014_security_manage_capability'),
    ]

    operations = [
        migrations.AddField(
            model_name='user', name='mfa_required',
            field=models.BooleanField(db_default=False, default=False, help_text="This person must enter a code from an authenticator app every time they sign in (when the Security page is set to 'Only people I have ticked'). Resetting is on this page.", verbose_name='Require multi-factor authentication'),
        ),
        migrations.AddField(model_name='user', name='mfa_secret', field=models.CharField(blank=True, db_default='', editable=False, max_length=200)),
        migrations.AddField(model_name='user', name='mfa_confirmed_at', field=models.DateTimeField(blank=True, editable=False, null=True)),
        migrations.AddField(model_name='user', name='mfa_backup_codes', field=models.TextField(blank=True, db_default='', editable=False)),
        migrations.AddField(model_name='user', name='mfa_last_step', field=models.BigIntegerField(db_default=0, default=0, editable=False)),
    ]
