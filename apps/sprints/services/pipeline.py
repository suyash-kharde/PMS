"""The sprint plan generation pipeline.

`run(plan_run)` executes the phases in order, persisting as it goes and updating the run /
step rows so the UI can poll progress. Driven by the background worker (`process_queue`).
"""

import traceback
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.intake.services import llm
from apps.notify import services as notify
from apps.projects.models import Activity

from ..models import (
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
from . import llm_planner as planner
from . import sources as sources_mod

VALID_FIELD_TYPES = set(planner.FIELD_TYPES)
VALID_MANDATORY = set(planner.MANDATORY)
VALID_TEST_TYPES = set(planner.TEST_TYPES)
VALID_RISK_CATEGORIES = set(planner.RISK_CATEGORIES)
VALID_SERVICE_CATEGORIES = set(planner.SERVICE_CATEGORIES)
VALID_SERVICE_PROVIDERS = set(planner.SERVICE_PROVIDERS)


class Cancelled(Exception):
    pass


# --------------------------------------------------------------------------- helpers
def _clamp(value, lo, hi, default):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def _step(run, order, key, label):
    step, _ = SprintPlanStep.objects.get_or_create(
        run=run, key=key, defaults={"order": order, "label": label}
    )
    return step


def _guard_cancelled(run):
    run.refresh_from_db(fields=["status"])
    if run.status == "cancelled":
        raise Cancelled()


def _parse_date(value, fallback):
    from datetime import date

    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return fallback
    return fallback


# --------------------------------------------------------------------------- phases
def _phase_collect(run, step):
    step.start()
    data = sources_mod.collect(run.project)
    run.sources = data["meta"]
    run.save(update_fields=["sources"])
    verdict = sources_mod.source_quality(data["meta"])
    step.finish(detail=verdict[:300])
    run.append_log(f"collected sources: {verdict}")
    return data


def _phase_research(run, step, data):
    step.start()
    system, user = planner.research_messages(data)
    brief, meta = planner.call_json(system, user, max_tokens=3200)
    run.research = brief
    run.save(update_fields=["research"])
    detail = (
        f"{len(brief.get('functional_areas', []))} areas · "
        f"{len(brief.get('entities', []))} entities · "
        f"{len(brief.get('build_phases', []))} phases · "
        f"{len(brief.get('external_services', []))} services · "
        f"{len(brief.get('risks', []))} risks"
    )
    step.finish(detail=detail, output=brief, model_name=meta.get("model", ""),
                tokens=meta.get("usage", {}).get("total_tokens", 0))
    run.append_log(f"research: {detail}")
    return brief


def _phase_plan(run, step, data, brief):
    step.start()
    system, user = planner.plan_messages(data, brief, run.options or {})
    plan, meta = planner.call_json(system, user, max_tokens=3500)
    sprint_specs = (plan.get("sprints") or [])[:planner.MAX_SPRINTS]

    weeks = _clamp((run.options or {}).get("sprint_weeks", 2), 1, 8, 2)
    cursor = _parse_date(data["project"].get("start_date"), timezone.localdate())
    existing = Sprint.objects.filter(project=run.project).count()

    created_reqs = []
    with transaction.atomic():
        for i, spec in enumerate(sprint_specs):
            start = _parse_date(spec.get("start_date"), cursor)
            end = _parse_date(spec.get("end_date"), start + timedelta(weeks=weeks))
            cursor = end + timedelta(days=1)
            sprint = Sprint.objects.create(
                project=run.project,
                name=(spec.get("name") or f"Sprint {existing + i + 1}")[:160],
                goal=(spec.get("goal") or "")[:4000],
                status="planning",
                start_date=start,
                end_date=end,
                owner=run.requested_by,
                generated_by=run,
            )
            for j, rspec in enumerate((spec.get("requirements") or [])[:planner.MAX_REQS_PER_SPRINT]):
                title = (rspec.get("title") or "").strip()
                if not title:
                    continue
                requirement = Requirement.objects.create(
                    project=run.project,
                    sprint=sprint,
                    title=title[:240],
                    user_story=(rspec.get("user_story") or "")[:2000],
                    priority=_clamp(rspec.get("priority"), 1, 4, 3),
                    estimate_points=_clamp(rspec.get("estimate_points"), 0, 100, 0),
                    status="draft",
                    reporter=run.requested_by,
                    order=j,
                    generated_by=run,
                )
                created_reqs.append((requirement, rspec))

    detail = f"{len(sprint_specs)} sprint(s), {len(created_reqs)} requirement(s)"
    step.finish(detail=detail, output={"sprints": len(sprint_specs), "requirements": len(created_reqs)},
                model_name=meta.get("model", ""))
    run.append_log(f"plan: {detail}")
    return created_reqs


def _apply_requirement(run, requirement, spec, brief, data, pending_deps):
    system, user = planner.requirement_messages(spec, brief, data)
    detail, meta = planner.call_json(system, user, max_tokens=3000)

    requirement.description = (detail.get("description") or requirement.description or "")[:8000]
    if detail.get("user_story"):
        requirement.user_story = detail["user_story"][:2000]
    if detail.get("user_story_id"):
        requirement.user_story_id = str(detail["user_story_id"])[:40]
    requirement.status = "ready"
    requirement.save(update_fields=["description", "user_story", "user_story_id", "status", "updated_at"])

    counts = {"criteria": 0, "field_specs": 0, "test_cases": 0, "risks": 0, "dependencies": 0}

    for order, c in enumerate((detail.get("acceptance_criteria") or [])[:planner.MAX_CRITERIA]):
        fmt = "gwt" if (c.get("format") == "gwt" or c.get("given")) else "statement"
        if fmt == "statement" and not (c.get("statement") or "").strip():
            continue
        if fmt == "gwt" and not (c.get("given") and c.get("when") and c.get("then")):
            continue
        AcceptanceCriterion.objects.create(
            requirement=requirement, order=order, format=fmt,
            statement=(c.get("statement") or "")[:2000],
            given_text=(c.get("given") or "")[:2000],
            when_text=(c.get("when") or "")[:2000],
            then_text=(c.get("then") or "")[:2000],
        )
        counts["criteria"] += 1

    for order, f in enumerate((detail.get("field_specs") or [])[:planner.MAX_FIELD_SPECS]):
        name = (f.get("name") or "").strip()
        if not name:
            continue
        ftype = f.get("field_type") if f.get("field_type") in VALID_FIELD_TYPES else "text"
        mandatory = f.get("mandatory") if f.get("mandatory") in VALID_MANDATORY else "no"
        FieldSpec.objects.create(
            requirement=requirement, order=order, name=name[:120], field_type=ftype,
            mandatory=mandatory, mandatory_condition=(f.get("mandatory_condition") or "")[:200],
            max_length=_clamp(f.get("max_length"), 1, 100000, None) if f.get("max_length") else None,
            validation=(f.get("validation") or "")[:200],
        )
        counts["field_specs"] += 1

    for t in (detail.get("unit_test_cases") or [])[:planner.MAX_TESTS]:
        title = (t.get("title") or "").strip()
        if not title:
            continue
        ttype = t.get("test_type") if t.get("test_type") in VALID_TEST_TYPES else "positive"
        UnitTestCase.objects.create(
            project=run.project, requirement=requirement, title=title[:240], test_type=ttype,
            preconditions=(t.get("preconditions") or "")[:4000],
            input_data=(t.get("input_data") or "")[:4000],
            steps=(t.get("steps") or "")[:4000],
            expected_result=(t.get("expected_result") or "")[:4000],
            generated_by=run,
        )
        counts["test_cases"] += 1

    for r in (detail.get("risks") or [])[:planner.MAX_RISKS]:
        title = (r.get("title") or "").strip()
        if not title:
            continue
        category = r.get("category") if r.get("category") in VALID_RISK_CATEGORIES else "other"
        RequirementRisk.objects.create(
            requirement=requirement, category=category, title=title[:200],
            description=(r.get("description") or "")[:4000],
            likelihood=_clamp(r.get("likelihood"), 1, 5, 3),
            impact=_clamp(r.get("impact"), 1, 5, 3),
            mitigation=(r.get("mitigation") or "")[:4000],
        )
        counts["risks"] += 1

    today = timezone.localdate()
    for d in (detail.get("dependencies") or [])[:planner.MAX_DEPS]:
        title = (d.get("title") or "").strip()
        if not title:
            continue
        kind = "external" if d.get("kind") == "external" else "internal"
        due = None
        if d.get("due_offset_days") is not None:
            due = today + timedelta(days=_clamp(d.get("due_offset_days"), 0, 365, 7))
        Dependency.objects.create(
            requirement=requirement, kind=kind, title=title[:200],
            description=(d.get("description") or "")[:4000],
            external_type=(d.get("external_type") or "")[:120],
            owner_note=(d.get("owner_note") or "")[:120],
            due_date=due,
            # depends_on resolved in the finalize phase from this title
        )
        # stash the wanted target for finalize
        if kind == "internal" and d.get("depends_on_title"):
            pending_deps.append((title, d["depends_on_title"]))
        counts["dependencies"] += 1

    return counts, meta


def _phase_services(run, step, data, brief, requirement_titles):
    step.start()
    system, user = planner.services_messages(data, brief, requirement_titles)
    payload, meta = planner.call_json(system, user, max_tokens=2500)
    specs = (payload.get("services") or [])[:planner.MAX_SERVICES]

    by_title = {r.title.lower(): r for r in Requirement.objects.filter(project=run.project, generated_by=run)}
    existing_names = {s.name.lower() for s in ExternalService.objects.filter(project=run.project)}
    made = 0
    for spec in specs:
        name = (spec.get("name") or "").strip()
        if not name or name.lower() in existing_names:
            continue
        category = spec.get("category") if spec.get("category") in VALID_SERVICE_CATEGORIES else "other"
        provided_by = spec.get("provided_by") if spec.get("provided_by") in VALID_SERVICE_PROVIDERS else "client"
        status = "configured" if spec.get("status") == "configured" else "identified"
        service = ExternalService.objects.create(
            project=run.project, category=category, name=name[:200],
            description=(spec.get("description") or "")[:4000],
            provided_by=provided_by, status=status, generated_by=run,
        )
        for wanted in spec.get("requirement_titles") or []:
            match = _match_requirement(by_title, wanted)
            if match:
                service.requirements.add(match)
        existing_names.add(name.lower())
        made += 1

    step.finish(detail=f"{made} external service(s)", output={"services": made}, model_name=meta.get("model", ""))
    run.append_log(f"services: {made} created")
    return made


def _match_requirement(reqs, wanted):
    """Exact title match, else the requirement whose title best overlaps `wanted`."""
    key = str(wanted or "").lower().strip()
    if not key:
        return None
    if key in reqs:
        return reqs[key]
    for title, req in reqs.items():
        if key in title or title in key:
            return req
    words = set(w for w in key.replace("-", " ").split() if len(w) > 3)
    best, best_score = None, 0
    for title, req in reqs.items():
        score = len(words & set(title.replace("-", " ").split()))
        if score > best_score:
            best, best_score = req, score
    return best if best_score >= 2 else None


def _phase_finalize(run, step, counts, pending_deps):
    step.start()
    # resolve internal dependencies by matching requirement titles created in this run
    resolved = 0
    reqs = {r.title.lower(): r for r in Requirement.objects.filter(project=run.project, generated_by=run)}
    for dep_title, target_title in pending_deps:
        target = _match_requirement(reqs, target_title)
        dep = Dependency.objects.filter(
            requirement__project=run.project, requirement__generated_by=run,
            title=dep_title, kind="internal", depends_on__isnull=True,
        ).first()
        if dep and target and target.pk != dep.requirement_id:
            dep.depends_on = target
            dep.save(update_fields=["depends_on"])
            resolved += 1

    run.result = counts
    run.summary = (
        f"Generated {counts['sprints']} sprint(s) and {counts['requirements']} requirement(s) "
        f"from {sources_mod.source_quality(run.sources)}: "
        f"{counts['criteria']} acceptance criteria, {counts['field_specs']} field specs, "
        f"{counts['test_cases']} unit test cases, {counts['risks']} risks, "
        f"{counts['dependencies']} dependencies ({resolved} internal links resolved), "
        f"{counts['services']} external services."
    )
    run.status = "done"
    run.finished_at = timezone.now()
    run.save(update_fields=["result", "summary", "status", "finished_at"])
    step.finish(detail=f"{resolved} dependency link(s) resolved")
    run.append_log("done: " + run.summary)

    Activity.log(
        run.requested_by,
        f"generated {counts['sprints']} sprint(s) / {counts['requirements']} requirement(s) "
        "from the Build Plan, Dev Instructions and BRD",
        project=run.project,
    )
    notify.sprint_plan_ready(run)


# --------------------------------------------------------------------------- orchestrator
def run(plan_run):
    plan_run.status = "running"
    plan_run.started_at = timezone.now()
    plan_run.phase = "Collecting project sources"
    plan_run.save(update_fields=["status", "started_at", "phase"])
    pending_deps = []

    counts = {k: 0 for k in
              ("sprints", "requirements", "criteria", "field_specs", "test_cases", "risks", "dependencies", "services")}

    collect_step = _step(plan_run, 0, "collect", "Collect project sources")
    research_step = _step(plan_run, 1, "research", "Deep research & analysis")
    plan_step = _step(plan_run, 2, "plan", "Draft the sprint plan")
    services_step = _step(plan_run, 90, "services", "Catalogue external services")
    finalize_step = _step(plan_run, 99, "finalize", "Finalise & link")

    try:
        data = _phase_collect(plan_run, collect_step)

        _guard_cancelled(plan_run)
        plan_run.phase = "Deep research & analysis"
        plan_run.save(update_fields=["phase"])
        brief = _phase_research(plan_run, research_step, data)

        _guard_cancelled(plan_run)
        plan_run.phase = "Drafting the sprint plan"
        plan_run.save(update_fields=["phase"])
        created_reqs = _phase_plan(plan_run, plan_step, data, brief)
        counts["sprints"] = plan_step.output.get("sprints", 0)
        counts["requirements"] = len(created_reqs)

        # pre-create the per-requirement steps so the UI shows the whole checklist
        req_steps = {}
        for idx, (requirement, _spec) in enumerate(created_reqs):
            req_steps[requirement.pk] = _step(
                plan_run, 10 + idx, f"elaborate:{requirement.key}", f"Detail {requirement.key} — {requirement.title}"
            )

        for requirement, spec in created_reqs:
            _guard_cancelled(plan_run)
            step = req_steps[requirement.pk]
            plan_run.phase = f"Detailing {requirement.key} — {requirement.title}"
            plan_run.save(update_fields=["phase"])
            step.start()
            try:
                sub_counts, meta = _apply_requirement(plan_run, requirement, spec, brief, data, pending_deps)
                for k, v in sub_counts.items():
                    counts[k] += v
                step.finish(
                    detail=(
                        f"{sub_counts['criteria']} criteria · {sub_counts['field_specs']} fields · "
                        f"{sub_counts['test_cases']} tests · {sub_counts['risks']} risks · "
                        f"{sub_counts['dependencies']} deps"
                    ),
                    output=sub_counts, model_name=meta.get("model", ""),
                )
                plan_run.append_log(f"{requirement.key}: {step.detail}")
            except llm.LLMError as exc:
                step.finish(status="failed", detail=str(exc)[:300])
                plan_run.append_log(f"{requirement.key}: FAILED — {exc}")

        _guard_cancelled(plan_run)
        plan_run.phase = "Cataloguing external services"
        plan_run.save(update_fields=["phase"])
        counts["services"] = _phase_services(
            plan_run, services_step, data, brief, [r.title for r, _ in created_reqs]
        )

        plan_run.phase = "Finalising"
        plan_run.save(update_fields=["phase"])
        _phase_finalize(plan_run, finalize_step, counts, pending_deps)

    except Cancelled:
        plan_run.status = "cancelled"
        plan_run.phase = "Cancelled"
        plan_run.finished_at = timezone.now()
        plan_run.result = counts
        plan_run.save(update_fields=["status", "phase", "finished_at", "result"])
        for step in plan_run.steps.filter(status__in=["pending", "running"]):
            step.finish(status="skipped")
        plan_run.append_log("cancelled by user")
    except Exception as exc:  # noqa: BLE001 - surface on the run
        plan_run.status = "failed"
        plan_run.phase = "Failed"
        plan_run.error = f"{type(exc).__name__}: {exc}"
        plan_run.result = counts
        plan_run.finished_at = timezone.now()
        plan_run.save(update_fields=["status", "phase", "error", "result", "finished_at"])
        for step in plan_run.steps.filter(status="running"):
            step.finish(status="failed", detail=str(exc)[:300])
        plan_run.append_log("FAILED:\n" + "".join(traceback.format_exception(exc))[-2000:])
        notify.sprint_plan_failed(plan_run)
    return plan_run


def drain_runs(stdout=None):
    for plan_run in SprintPlanRun.objects.filter(status="queued").order_by("created_at"):
        if stdout:
            stdout.write(f"[plan-run {plan_run.pk}] {plan_run.project.key} — generating sprint plan")
        run(plan_run)
        plan_run.refresh_from_db()
        if stdout:
            stdout.write(f"[plan-run {plan_run.pk}] -> {plan_run.status}")
