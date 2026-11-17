from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Capability, Role, RoleCapability, Team, User, UserCapability


class RoleCapabilityInline(admin.TabularInline):
    model = RoleCapability
    extra = 1


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "rank", "is_system")
    list_filter = ("is_system",)
    inlines = [RoleCapabilityInline]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Capability)
class CapabilityAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "category")
    list_filter = ("category",)
    search_fields = ("code", "label")


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "lead")


class UserCapabilityInline(admin.TabularInline):
    model = UserCapability
    fk_name = "user"
    extra = 1


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = [UserCapabilityInline]
    list_display = ("username", "display_name", "role", "team", "manager", "is_active")
    list_filter = ("role", "team", "is_active", "is_staff")
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Organisation", {"fields": ("role", "team", "manager", "title", "phone", "color")}),
    )
