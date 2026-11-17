"""Project Links can be opened through Planwise (/sites/<name>/). Additive; existing links are unchanged."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0012_projectlink"),
    ]

    operations = [
        migrations.AddField(
            model_name="projectlink",
            name="via_gateway",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="projectlink",
            name="site_name",
            field=models.CharField(blank=True, help_text="Address under /sites/, e.g. gepdec_solar (lowercase, digits, - and _)", max_length=32),
        ),
        migrations.AddField(
            model_name="projectlink",
            name="gateway_options",
            field=models.JSONField(blank=True, default=dict, help_text="rewrite_html, rewrite_js, isolate_storage, insecure, sync_tokens, mounts"),
        ),
        migrations.AddField(
            model_name="projectlink",
            name="gateway_synced_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="projectlink",
            name="gateway_error",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddConstraint(
            model_name="projectlink",
            constraint=models.UniqueConstraint(condition=models.Q(("site_name", ""), _negated=True), fields=("site_name",), name="projectlink_site_name_unique"),
        ),
    ]
