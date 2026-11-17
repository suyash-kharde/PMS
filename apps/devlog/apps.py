from django.apps import AppConfig


class DevlogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.devlog"
    label = "devlog"
    verbose_name = "Claude Code dev log"
