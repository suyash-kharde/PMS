import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def convert_names_to_users(apps, schema_editor):
    """Match each existing free-text step name to a real account by username (case-insensitive).
    A step with no matching account is deleted rather than left dangling — there were only two
    such rows in the entire system when this migration was written, both exploratory test data
    from building this feature, and both matched a real username cleanly."""
    ApprovalStep = apps.get_model("vms", "ApprovalStep")
    User = apps.get_model("accounts", "User")
    for step in ApprovalStep.objects.all():
        user = User.objects.filter(username__iexact=step.name).first()
        if user:
            step.approver_id = user.pk
            step.save(update_fields=["approver_id"])
        else:
            step.delete()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("vms", "0004_alter_projectvm_webhook_token_prefix"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="approvalstep",
            name="approver",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="approval_steps",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(convert_names_to_users, noop_reverse),
        migrations.RemoveField(
            model_name="approvalstep",
            name="name",
        ),
        migrations.AlterField(
            model_name="approvalstep",
            name="approver",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="approval_steps",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
