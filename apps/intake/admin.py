from django.contrib import admin

from .models import BRD, BRDVersion, MediaAsset, Speaker, TimelineEntry, TranscriptSegment


class SpeakerInline(admin.TabularInline):
    model = Speaker
    extra = 0


class MediaInline(admin.TabularInline):
    model = MediaAsset
    extra = 0


@admin.register(TimelineEntry)
class TimelineEntryAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "kind", "status", "author", "occurred_at", "brd_applied")
    list_filter = ("kind", "status", "project", "brd_applied")
    search_fields = ("title", "body", "transcript")
    inlines = [MediaInline, SpeakerInline]


class VersionInline(admin.TabularInline):
    model = BRDVersion
    extra = 0
    readonly_fields = ("number", "summary", "model_name", "created_at")
    fields = readonly_fields


@admin.register(BRD)
class BRDAdmin(admin.ModelAdmin):
    list_display = ("project", "updated_at")
    inlines = [VersionInline]


@admin.register(BRDVersion)
class BRDVersionAdmin(admin.ModelAdmin):
    list_display = ("brd", "number", "summary", "model_name", "created_by", "created_at")
    list_filter = ("brd", "model_name")


admin.site.register([TranscriptSegment, Speaker, MediaAsset])
