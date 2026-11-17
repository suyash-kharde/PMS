from django.contrib import admin

from .models import ModelProvider


@admin.register(ModelProvider)
class ModelProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "model_name", "host", "is_default", "is_active", "last_status")
    list_filter = ("kind", "is_active", "is_default", "last_status")
    search_fields = ("name", "base_url", "model_name")
    readonly_fields = ("last_checked_at", "last_status", "last_error", "last_latency_ms", "last_models_seen")
