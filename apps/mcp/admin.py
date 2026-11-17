from django.contrib import admin

from .models import ProjectMCPToken


@admin.register(ProjectMCPToken)
class ProjectMCPTokenAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "project",
        "token_prefix",
        "status_label",
        "allow_ticket_write",
        "allow_sprint_write",
        "created_by",
        "last_used_at",
        "created_at",
    )
    list_filter = ("allow_ticket_write", "allow_sprint_write", "project")
    search_fields = ("name", "token_prefix", "project__key", "project__name", "created_by__username")
    readonly_fields = ("token_hash", "token_prefix", "last_used_at", "created_at")
    autocomplete_fields = ()

    @admin.display(description="Status")
    def status_label(self, obj):
        return obj.status_label

    def has_add_permission(self, request):
        # a usable link can only be minted from the project's Claude Code tab (raw token shown once)
        return False
