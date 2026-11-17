from django.contrib import admin

from .models import PenpotInvite, PenpotWorkspace


class PenpotInviteInline(admin.TabularInline):
    model = PenpotInvite
    extra = 0
    fields = ("email", "role", "user", "invited_by", "created_at")
    readonly_fields = ("created_at",)


@admin.register(PenpotWorkspace)
class PenpotWorkspaceAdmin(admin.ModelAdmin):
    list_display = ("project", "status", "phase", "team_name", "file_name", "provisioned_at")
    list_filter = ("status",)
    search_fields = ("project__key", "project__name", "bot_email", "team_id", "penpot_project_id", "file_id")
    readonly_fields = (
        "masked_password", "masked_token", "profile_id", "team_id", "penpot_project_id",
        "file_id", "share_link_id", "share_url", "provisioned_at", "synced_at",
        "created_at", "updated_at", "workspace_url", "dashboard_url",
    )
    exclude = ("bot_password", "access_token")
    inlines = [PenpotInviteInline]

    @admin.display(description="Bot password")
    def masked_password(self, obj):
        return obj.masked_password

    @admin.display(description="Access token")
    def masked_token(self, obj):
        return obj.masked_token
