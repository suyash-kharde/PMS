from django.contrib import admin

from .models import Notification, PushSubscription


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "user_agent", "created_at", "last_used_at", "failures")
    list_filter = ("user",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "title", "pushed", "read_at", "created_at")
    list_filter = ("kind", "pushed", "user")
    search_fields = ("title", "body")
