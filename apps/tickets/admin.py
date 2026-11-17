from django.contrib import admin

from .models import Ticket, TicketAttachment, TicketComment, TicketEvent


class TicketCommentInline(admin.TabularInline):
    model = TicketComment
    extra = 0


class TicketAttachmentInline(admin.TabularInline):
    model = TicketAttachment
    extra = 0


class TicketEventInline(admin.TabularInline):
    model = TicketEvent
    extra = 0
    readonly_fields = ("actor", "verb", "created_at")
    can_delete = False


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("key", "title", "project", "type", "status", "priority", "assignee", "created_at")
    list_filter = ("type", "status", "priority", "project")
    search_fields = ("title", "description", "component")
    autocomplete_fields = ()
    inlines = [TicketCommentInline, TicketAttachmentInline, TicketEventInline]

    @admin.display(description="Key")
    def key(self, obj):
        return obj.key


admin.site.register([TicketComment, TicketAttachment, TicketEvent])
