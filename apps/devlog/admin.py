from django.contrib import admin

from .models import DevUpdate


@admin.register(DevUpdate)
class DevUpdateAdmin(admin.ModelAdmin):
    list_display = (
        "headline", "project", "kind", "status", "milestone", "milestone_done",
        "source", "created_by", "created_at",
    )
    list_filter = ("kind", "status", "source", "milestone_done", "project")
    search_fields = ("headline", "details", "milestone", "branch", "commit")
    readonly_fields = ("created_at", "updated_at", "via_token")
    autocomplete_fields = ()
