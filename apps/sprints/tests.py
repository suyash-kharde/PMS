from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Capability, Role, RoleCapability, User
from apps.accounts.testing import skip_password_prompt
from apps.intake.services.llm import LLMError
from apps.notify.models import Notification
from apps.projects.models import Project

from .models import (
    AcceptanceCriterion,
    Dependency,
    ExternalService,
    FieldSpec,
    Requirement,
    RequirementRisk,
    Sprint,
    SprintPlanRun,
    UnitTestCase,
)
from .selectors import visible_requirements
from .services import pipeline


def _cap(code, role, scope):
    capability, _ = Capability.objects.get_or_create(code=code, defaults={"label": code, "category": "sprints"})
    RoleCapability.objects.get_or_create(role=role, capability=capability, defaults={"scope": scope})


class SprintModelTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.project = Project.objects.create(name="Platform Rebuild", key="PLAT")
        self.other = Project.objects.create(name="Mobile", key="MOB")

    def test_per_project_number_sequences(self):
        s1 = Sprint.objects.create(project=self.project, name="S1")
        s2 = Sprint.objects.create(project=self.project, name="S2")
        s_other = Sprint.objects.create(project=self.other, name="S1")
        r1 = Requirement.objects.create(project=self.project, title="R1")
        r2 = Requirement.objects.create(project=self.project, title="R2")
        tc1 = UnitTestCase.objects.create(project=self.project, title="TC1")
        self.assertEqual((s1.number, s2.number, s_other.number), (1, 2, 1))
        self.assertEqual((r1.number, r2.number), (1, 2))
        self.assertEqual(s2.key, "PLAT-S2")
        self.assertEqual(r2.key, "PLAT-R2")
        self.assertEqual(tc1.key, "PLAT-TC1")

    def test_criterion_clean_enforces_format(self):
        req = Requirement.objects.create(project=self.project, title="Add participant")
        statement = AcceptanceCriterion(requirement=req, format="statement", statement="")
        with self.assertRaises(Exception):
            statement.clean()
        gwt = AcceptanceCriterion(requirement=req, format="gwt", given_text="x", when_text="", then_text="z")
        with self.assertRaises(Exception):
            gwt.clean()
        ok = AcceptanceCriterion(requirement=req, format="statement", statement="User can enter first name.")
        ok.clean()  # no raise

    def test_sprint_progress(self):
        sprint = Sprint.objects.create(project=self.project, name="S1")
        Requirement.objects.create(project=self.project, sprint=sprint, title="a", status="done")
        Requirement.objects.create(project=self.project, sprint=sprint, title="b", status="draft")
        self.assertEqual(sprint.progress, 50)


class SprintFlowTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.role = Role.objects.create(name="Staff", slug="staff")
        for code in ("sprint.view", "sprint.manage", "testcase.manage", "service.manage"):
            _cap(code, self.role, "all")
        self.project = Project.objects.create(name="Platform Rebuild", key="PLAT")
        self.owner = User.objects.create_user("marco", password="x", role=self.role)
        self.dev = User.objects.create_user("lena", password="x", role=self.role)
        self.project.members.add(self.owner, self.dev)

    def test_create_requirement_notifies_assignee(self):
        self.client.force_login(self.owner)
        sprint = Sprint.objects.create(project=self.project, name="S1")
        resp = self.client.post(
            reverse("sprints:requirement_create", args=[sprint.pk]),
            {"title": "Add Participant", "priority": 1, "status": "draft", "estimate_points": 3,
             "assignee": self.dev.pk, "sprint": sprint.pk},
            HTTP_HOST="localhost",
        )
        self.assertEqual(resp.status_code, 302)
        req = Requirement.objects.get(title="Add Participant")
        self.assertEqual(req.reporter, self.owner)
        self.assertTrue(
            Notification.objects.filter(user=self.dev, kind="requirement_assigned").exists()
        )

    def test_add_acceptance_criterion_inline(self):
        req = Requirement.objects.create(project=self.project, title="Add Participant")
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("sprints:child_add", args=["criteria", req.pk]),
            {"format": "statement", "statement": "User can enter first name.", "order": 0},
            HTTP_HOST="localhost",
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(req.criteria.count(), 1)

    def test_execute_failing_test_case_notifies_requirement_people(self):
        req = Requirement.objects.create(
            project=self.project, title="Add Participant", assignee=self.dev, reporter=self.owner
        )
        tc = UnitTestCase.objects.create(project=self.project, requirement=req, title="Save valid")
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("sprints:test_case_execute", args=[tc.pk]),
            {"status": "fail", "actual_result": "500 error", "comments": ""},
            HTTP_HOST="localhost",
        )
        self.assertEqual(resp.status_code, 302)
        tc.refresh_from_db()
        self.assertEqual(tc.status, "fail")
        self.assertEqual(tc.developer, self.owner)
        self.assertIsNotNone(tc.executed_on)
        self.assertTrue(Notification.objects.filter(user=self.dev, kind="testcase_result").exists())

    def test_visibility_scoping_for_guest(self):
        guest_role = Role.objects.create(name="Guest", slug="guest2")
        _cap("sprint.view", guest_role, "own")
        outsider = User.objects.create_user("out", password="x", role=guest_role)
        mine = Requirement.objects.create(project=self.project, title="mine", reporter=outsider)
        Requirement.objects.create(project=self.project, title="theirs", reporter=self.owner)
        visible = visible_requirements(outsider)
        self.assertIn(mine, visible)
        self.assertEqual(visible.count(), 1)


# --------------------------------------------------------------------------- AI generation
def _fake_call_json(system, user, **kwargs):
    """Dispatch canned JSON on which prompt is being run."""
    meta = {"model": "mock", "usage": {"total_tokens": 10}}
    if "structured research brief" in system.lower():
        return {
            "overview": "A participant management module.",
            "functional_areas": [{"name": "Participants", "summary": "CRUD for participants"}],
            "entities": [{"name": "Participant", "key_fields": ["first_name"], "screens": ["Add Participant"]}],
            "build_phases": [{"name": "Phase 1", "goal": "participants", "covers": ["Participants"]}],
            "cross_cutting_rules": ["Validate mandatory fields"],
            "external_services": [{"category": "smtp", "name": "SMTP relay", "why": "email", "provided_by": "client"}],
            "risks": [{"category": "client_dependency", "title": "Client data", "why": "import file"}],
            "open_questions": [],
        }, meta
    if "drawing up the sprint plan" in system.lower():
        return {
            "sprints": [{
                "name": "Sprint 1 — Participants", "goal": "participant CRUD",
                "start_date": "2026-09-01", "end_date": "2026-09-14", "order": 1,
                "requirements": [
                    {"title": "Add Participant", "functional_area": "Participants", "priority": 1,
                     "estimate_points": 5, "user_story": "As an operator I want to add a participant"},
                    {"title": "Participant list", "functional_area": "Participants", "priority": 3,
                     "estimate_points": 3, "user_story": "As an operator I want a list"},
                ],
            }]
        }, meta
    if "detailing one requirement" in system.lower():
        add = "Title: Add Participant" in user
        return {
            "description": "Create a participant with validated details.",
            "user_story": "As an operator I want to add a participant so that they can take part.",
            "user_story_id": "PLAT-STORY-1",
            "acceptance_criteria": [
                {"format": "statement", "statement": "User can enter first name."},
                {"format": "gwt", "given": "a valid user is logged in",
                 "when": "the user selects Add Participant", "then": "the form is displayed"},
            ],
            "field_specs": ([{"name": "First Name", "field_type": "text", "mandatory": "yes",
                              "mandatory_condition": "", "max_length": 50, "validation": "Alphabetic"}] if add else []),
            "unit_test_cases": [
                {"title": "Save valid participant", "test_type": "positive", "preconditions": "logged in",
                 "input_data": "valid", "steps": "1. fill\n2. submit", "expected_result": "saved"},
                {"title": "Missing first name", "test_type": "validation", "preconditions": "logged in",
                 "input_data": "empty first name", "steps": "1. submit", "expected_result": "error shown"},
            ],
            "risks": [{"category": "client_dependency", "title": "Import file", "description": "needs CSV",
                       "likelihood": 4, "impact": 3, "mitigation": "chase client"}],
            "dependencies": ([] if add else [
                {"kind": "internal", "title": "Needs Add Participant", "description": "",
                 "depends_on_title": "Add Participant", "external_type": "", "owner_note": "", "due_offset_days": 5},
                {"kind": "external", "title": "SMTP credentials", "description": "",
                 "depends_on_title": "", "external_type": "SMTP credentials", "owner_note": "Client", "due_offset_days": 10},
            ]),
        }, meta
    if "cataloguing the external" in system.lower():
        return {
            "services": [
                {"category": "smtp", "name": "SMTP relay credentials", "description": "mail relay",
                 "provided_by": "client", "status": "identified", "requirement_titles": ["Add Participant"]},
                {"category": "stripe", "name": "Stripe account", "description": "billing",
                 "provided_by": "client", "status": "requested", "requirement_titles": []},
            ]
        }, meta
    raise AssertionError("unexpected prompt: " + system[:60])


class SprintPlanGenerationTests(TestCase):
    def setUp(self):
        skip_password_prompt(self)
        self.role = Role.objects.create(name="PM", slug="pm")
        for code in ("sprint.view", "sprint.manage", "testcase.manage", "service.manage", "sprint.generate"):
            _cap(code, self.role, "all")
        self.project = Project.objects.create(name="Platform Rebuild", key="PLAT")
        self.pm = User.objects.create_user("marco", password="x", role=self.role)
        self.project.members.add(self.pm)

    def _run(self):
        run = SprintPlanRun.objects.create(project=self.project, requested_by=self.pm, status="queued")
        with patch("apps.sprints.services.pipeline.planner.call_json", side_effect=_fake_call_json):
            pipeline.run(run)
        run.refresh_from_db()
        return run

    def test_full_pipeline_builds_the_hierarchy(self):
        run = self._run()
        self.assertEqual(run.status, "done")
        self.assertEqual(Sprint.objects.filter(project=self.project, generated_by=run).count(), 1)
        reqs = Requirement.objects.filter(project=self.project, generated_by=run)
        self.assertEqual(reqs.count(), 2)
        add = reqs.get(title="Add Participant")
        self.assertEqual(add.status, "ready")
        self.assertTrue(add.criteria.filter(format="statement").exists())
        self.assertTrue(add.criteria.filter(format="gwt").exists())
        self.assertTrue(add.field_specs.exists())
        self.assertEqual(UnitTestCase.objects.filter(generated_by=run).count(), 4)
        self.assertTrue(RequirementRisk.objects.filter(requirement__in=reqs).exists())
        # internal dependency resolved to the real requirement
        dep = Dependency.objects.get(kind="internal", requirement__title="Participant list")
        self.assertEqual(dep.depends_on, add)
        self.assertTrue(ExternalService.objects.filter(project=self.project, generated_by=run).count() >= 1)
        self.assertTrue(Notification.objects.filter(user=self.pm, kind="sprint_plan").exists())

    def test_one_requirement_failure_does_not_abort_run(self):
        calls = {"n": 0}

        def flaky(system, user, **kwargs):
            if "detailing one requirement" in system.lower():
                calls["n"] += 1
                if calls["n"] == 1:
                    raise LLMError("boom")
            return _fake_call_json(system, user, **kwargs)

        run = SprintPlanRun.objects.create(project=self.project, requested_by=self.pm, status="queued")
        with patch("apps.sprints.services.pipeline.planner.call_json", side_effect=flaky):
            pipeline.run(run)
        run.refresh_from_db()
        self.assertEqual(run.status, "done")
        self.assertTrue(run.steps.filter(status="failed").exists())
        self.assertTrue(run.steps.filter(key__startswith="elaborate:", status="done").exists())

    def test_discard_removes_generated_rows(self):
        run = self._run()
        self.client.force_login(self.pm)
        resp = self.client.post(reverse("sprints:plan_run_discard", args=[run.pk]), HTTP_HOST="localhost")
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Sprint.objects.filter(project=self.project).exists())
        self.assertFalse(Requirement.objects.filter(project=self.project).exists())
        self.assertFalse(UnitTestCase.objects.exists())
        self.assertFalse(SprintPlanRun.objects.filter(pk=run.pk).exists())

    def test_cancel_halts_before_next_phase(self):
        run = SprintPlanRun.objects.create(project=self.project, requested_by=self.pm, status="queued")

        def cancel_after_research(system, user, **kwargs):
            result = _fake_call_json(system, user, **kwargs)
            if "structured research brief" in system.lower():
                SprintPlanRun.objects.filter(pk=run.pk).update(status="cancelled")
            return result

        with patch("apps.sprints.services.pipeline.planner.call_json", side_effect=cancel_after_research):
            pipeline.run(run)
        run.refresh_from_db()
        self.assertEqual(run.status, "cancelled")
        self.assertFalse(Sprint.objects.filter(project=self.project).exists())

    def test_status_endpoint_shape(self):
        run = self._run()
        self.client.force_login(self.pm)
        resp = self.client.get(reverse("sprints:api_plan_run_status", args=[run.pk]), HTTP_HOST="localhost")
        data = resp.json()
        self.assertEqual(data["status"], "done")
        self.assertTrue(data["terminal"])
        self.assertIn("sprints", data["result"])
        self.assertTrue(any(s["key"] == "research" for s in data["steps"]))
