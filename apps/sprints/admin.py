from django.contrib import admin

from .models import (
    AcceptanceCriterion,
    Dependency,
    ExternalService,
    FieldSpec,
    Requirement,
    RequirementRisk,
    Sprint,
    SprintPlanRun,
    SprintPlanStep,
    UnitTestCase,
)


class AcceptanceCriterionInline(admin.TabularInline):
    model = AcceptanceCriterion
    extra = 0


class FieldSpecInline(admin.TabularInline):
    model = FieldSpec
    extra = 0


class RequirementRiskInline(admin.TabularInline):
    model = RequirementRisk
    extra = 0
    fk_name = "requirement"


class DependencyInline(admin.TabularInline):
    model = Dependency
    extra = 0
    fk_name = "requirement"


class RequirementInline(admin.TabularInline):
    model = Requirement
    extra = 0
    fields = ("title", "status", "priority", "assignee", "estimate_points")
    show_change_link = True


@admin.register(Sprint)
class SprintAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "project", "status", "start_date", "end_date")
    list_filter = ("status", "project")
    search_fields = ("name", "goal")
    inlines = [RequirementInline]

    @admin.display(description="Key")
    def key(self, obj):
        return obj.key


@admin.register(Requirement)
class RequirementAdmin(admin.ModelAdmin):
    list_display = ("key", "title", "project", "sprint", "status", "priority", "assignee")
    list_filter = ("status", "priority", "project", "sprint")
    search_fields = ("title", "description", "user_story", "user_story_id")
    inlines = [AcceptanceCriterionInline, FieldSpecInline, RequirementRiskInline, DependencyInline]

    @admin.display(description="Key")
    def key(self, obj):
        return obj.key


@admin.register(UnitTestCase)
class UnitTestCaseAdmin(admin.ModelAdmin):
    list_display = ("key", "title", "project", "requirement", "test_type", "status", "developer", "executed_on")
    list_filter = ("status", "test_type", "project")
    search_fields = ("title", "steps", "expected_result")

    @admin.display(description="Key")
    def key(self, obj):
        return obj.key


@admin.register(ExternalService)
class ExternalServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "category", "provided_by", "status", "owner", "expected_date")
    list_filter = ("status", "provided_by", "category", "project")
    search_fields = ("name", "description")
    filter_horizontal = ("requirements",)


class SprintPlanStepInline(admin.TabularInline):
    model = SprintPlanStep
    extra = 0
    readonly_fields = ("key", "label", "status", "detail", "model_name", "tokens")
    can_delete = False


@admin.register(SprintPlanRun)
class SprintPlanRunAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "status", "phase", "requested_by", "created_at", "finished_at")
    list_filter = ("status", "project")
    readonly_fields = ("sources", "research", "result", "log", "created_at", "started_at", "finished_at")
    inlines = [SprintPlanStepInline]


admin.site.register([AcceptanceCriterion, FieldSpec, RequirementRisk, Dependency])
