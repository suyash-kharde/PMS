from django.contrib import admin

from .models import (
    Activity, Comment, Customer, Label, Milestone, Project, ProjectDocument, ProjectMembership, Task, Workflow,
    WorkflowState,
)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "industry", "account_manager", "is_active")
    list_filter = ("is_active", "industry")
    search_fields = ("name", "code")


class WorkflowStateInline(admin.TabularInline):
    model = WorkflowState
    extra = 1


@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ("name", "is_default")
    inlines = [WorkflowStateInline]


class MembershipInline(admin.TabularInline):
    model = ProjectMembership
    extra = 1


class MilestoneInline(admin.TabularInline):
    model = Milestone
    extra = 1


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "customer", "status", "priority", "owner", "team", "due_date", "archived")
    list_filter = ("customer", "status", "priority", "team", "archived")
    search_fields = ("key", "name")
    inlines = [MembershipInline, MilestoneInline]


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "state", "priority", "assignee", "due_date")
    list_filter = ("project", "state", "priority")
    search_fields = ("title",)
    autocomplete_fields = ()


@admin.register(ProjectDocument)
class ProjectDocumentAdmin(admin.ModelAdmin):
    list_display = ("project", "kind", "updated_by", "updated_at")
    list_filter = ("kind",)


admin.site.register([Label, Comment, Activity, Milestone])
admin.site.site_header = "Planwise administration"
