"""Prompt construction + robust JSON extraction for the sprint plan generator.

All model calls go through `call_json`, which asks for JSON only, tolerates ``` fences and
<think> blocks, and retries once before giving up.
"""

import json

from apps.intake.services import llm

# Choice codes the model must stick to (mirrors apps/sprints/models.py)
FIELD_TYPES = ["text", "textarea", "email", "number", "phone", "date", "datetime", "boolean", "select", "file"]
MANDATORY = ["yes", "no", "depends"]
TEST_TYPES = ["positive", "negative", "validation", "boundary", "integration", "error_handling"]
RISK_CATEGORIES = [
    "third_party_integration", "client_dependency", "technical", "resourcing",
    "schedule", "security", "data", "other",
]
DEPENDENCY_KINDS = ["internal", "external"]
SERVICE_CATEGORIES = [
    "stripe", "payment_gateway", "subscription_provider", "smtp", "email_service", "sms_gateway",
    "cloud_account", "domain", "api_credentials", "software_license", "external_database",
    "client_data", "hosting", "server_access", "other",
]
SERVICE_PROVIDERS = ["client", "internal", "third_party"]

# hard safety caps
MAX_SPRINTS = 6
MAX_REQS_PER_SPRINT = 10
MAX_CRITERIA = 8
MAX_FIELD_SPECS = 15
MAX_TESTS = 6
MAX_RISKS = 5
MAX_DEPS = 5
MAX_SERVICES = 20


def _extract_json(text):
    text = llm.strip_reasoning(text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    text = text.strip()
    # find the first balanced { } or [ ]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    return json.loads(text)  # let it raise


def call_json(system, user, *, max_tokens=2500, temperature=0.15):
    """-> (obj, meta). Raises llm.LLMError after a failed retry."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    text, meta = llm.chat(messages, max_tokens=max_tokens, temperature=temperature)
    try:
        return _extract_json(text), meta
    except (ValueError, json.JSONDecodeError):
        messages.append({"role": "assistant", "content": text[:1500]})
        messages.append({
            "role": "user",
            "content": "That was not valid JSON. Reply with ONLY the JSON object, no prose, no code fences.",
        })
        text, meta = llm.chat(messages, max_tokens=max_tokens, temperature=0.0)
        try:
            return _extract_json(text), meta
        except (ValueError, json.JSONDecodeError) as exc:
            raise llm.LLMError(f"model did not return usable JSON: {exc}") from exc


# --------------------------------------------------------------------------- prompts
_SOURCES_TEMPLATE = """PROJECT
{key} — {name}
Status: {status} | Priority: {priority} | Dates: {start_date} to {due_date}
Description: {description}

PROJECT ROSTER
{roster}

BUILD PLAN (PLAN.md)
{build_plan}

DEVELOPMENT INSTRUCTIONS
{dev_instructions}

TECH STACK
{tech_stack}

BUSINESS REQUIREMENT DOCUMENT
{brd}

PROJECT TIMELINE (notes, decisions, change requests)
{timeline}

OPEN TICKETS (features / change requests)
{tickets}
"""


def sources_prompt(sources):
    p = sources["project"]
    tickets = "\n".join(f"- {t['key']} [{t['type']}] {t['title']}" for t in sources["tickets"]) or "(none)"
    return _SOURCES_TEMPLATE.format(
        key=p["key"], name=p["name"], status=p["status"], priority=p["priority"],
        start_date=p["start_date"] or "—", due_date=p["due_date"] or "—",
        description=p["description"] or "—",
        roster=sources["roster"] or "(none)",
        build_plan=sources["build_plan"] or "(not written yet — derive phases from the BRD)",
        dev_instructions=sources["dev_instructions"] or "(none)",
        tech_stack=sources["tech_stack"] or "(none)",
        brd=sources["brd"] or "(none yet — derive requirements from the timeline and tickets)",
        timeline=sources["timeline"] or "(none)",
        tickets=tickets,
    )


RESEARCH_SYSTEM = """You are a senior delivery lead and business analyst doing a deep \
analysis pass over a software project BEFORE sprint planning. You are given the project's \
Build Plan (PLAN.md), Development Instructions, Tech Stack, Business Requirement Document \
(BRD), roster, timeline and open tickets.

Analyse them together and produce a structured research brief. Rules:
- Derive `build_phases` from the Build Plan's implementation phases when it has them; \
otherwise infer phases from the BRD's functional scope.
- Derive `functional_areas` and `entities` (with key fields and the screens/forms each \
implies) from the BRD's functional requirements.
- Derive `cross_cutting_rules` from the Development Instructions and the BRD's \
non-functional requirements (validation conventions, auth, testing expectations, audit, \
performance).
- Derive `external_services` from the Tech Stack and BRD — anything the team needs an \
account, credential, gateway, licence or client-supplied datum for (payment provider, \
SMTP, SMS, cloud account, domain, third-party API, external DB, client data).
- Derive `risks` — especially third-party-integration risk and client-dependency risk.

Return ONLY this JSON object:
{
  "overview": "2-3 sentence synthesis of what is being built",
  "functional_areas": [{"name": "...", "summary": "..."}],
  "entities": [{"name": "...", "key_fields": ["..."], "screens": ["..."]}],
  "build_phases": [{"name": "...", "goal": "...", "covers": ["functional area names"]}],
  "cross_cutting_rules": ["..."],
  "external_services": [{"category": "one of %s", "name": "...", "why": "...", "provided_by": "client|internal|third_party"}],
  "risks": [{"category": "one of %s", "title": "...", "why": "..."}],
  "open_questions": ["..."]
}""" % (SERVICE_CATEGORIES, RISK_CATEGORIES)


def research_messages(sources):
    return RESEARCH_SYSTEM, sources_prompt(sources) + "\n\nProduce the research brief now."


PLAN_SYSTEM = """You are a delivery lead drawing up the sprint plan for a software project. \
You are given a research brief (functional areas, entities, build phases, risks) and the \
project's dates.

Produce an ordered list of sprints that delivers the whole functional scope. Rules:
- Follow the Build Plan's phase order. One sprint per phase is a good default; split a \
large phase into two sprints if it clearly needs it.
- At most %d sprints, at most %d requirements per sprint.
- Each requirement is a single testable capability (e.g. "Add participant", "Validate \
mandatory fields", "Participant list with search"). Give it a priority 1-4 (1 = critical) \
and a rough story-point estimate (1,2,3,5,8,13).
- Sprint dates: start from the project start date (or today); each sprint is the number of \
weeks given in OPTIONS, back to back.

Return ONLY this JSON:
{
  "sprints": [
    {"name": "...", "goal": "...", "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD", "order": 1,
     "requirements": [
       {"title": "...", "functional_area": "...", "priority": 2, "estimate_points": 3,
        "user_story": "As a <role> I want <goal> so that <benefit>"}
     ]}
  ]
}""" % (MAX_SPRINTS, MAX_REQS_PER_SPRINT)


def plan_messages(sources, research, options):
    import json as _json

    user = (
        sources_prompt(sources)
        + "\n\nRESEARCH BRIEF\n"
        + _json.dumps(research, indent=1)
        + f"\n\nOPTIONS\nsprint length: {options.get('sprint_weeks', 2)} week(s); "
        + f"target sprint count: {options.get('target_sprints') or 'your call'}.\n\n"
        + "Produce the sprint plan now."
    )
    return PLAN_SYSTEM, user


REQUIREMENT_SYSTEM = """You are a senior business analyst detailing ONE requirement so a \
developer can build and unit-test it. You are given the requirement, the project's research \
brief, the BRD and the Development Instructions.

Produce, for this requirement only:
- a concrete `description` and a `user_story` ("As a ... I want ... so that ...").
- `acceptance_criteria`: 3-%d items. Use short imperative `statement` items ("User can \
enter first name.", "Mandatory fields must be validated.", "Invalid data must display an \
appropriate validation message.") AND include at least one structured `gwt` item \
(given/when/then).
- `field_specs`: when the requirement is a form or data-entry screen, one row per field \
with type, whether it is mandatory (yes/no/depends + condition), configured max length and \
the validation rule. Use the BRD's field tables when it has them. Omit if the requirement \
has no form.
- `unit_test_cases`: 2-%d cases a developer runs before QA — mix `positive`, `negative`, \
`validation`, `boundary`. Each has preconditions, input_data, steps (one per line) and \
expected_result.
- `risks`: 0-%d, only real ones (third_party_integration, client_dependency, technical, ...).
- `dependencies`: 0-%d. `internal` = another requirement that must ship first (name it in \
`depends_on_title`). `external` = a client/third-party thing (set `external_type` and, if \
known, `owner_note`); `due_offset_days` = days from today it is needed.

Allowed codes — field_type: %s; mandatory: %s; test_type: %s; risk category: %s; \
dependency kind: %s.

Return ONLY this JSON:
{
  "description": "...", "user_story": "...", "user_story_id": "",
  "acceptance_criteria": [
    {"format": "statement", "statement": "..."},
    {"format": "gwt", "given": "...", "when": "...", "then": "..."}
  ],
  "field_specs": [
    {"name": "...", "field_type": "text", "mandatory": "yes", "mandatory_condition": "",
     "max_length": 50, "validation": "Alphabetic / configured length"}
  ],
  "unit_test_cases": [
    {"title": "...", "test_type": "positive", "preconditions": "...", "input_data": "...",
     "steps": "1. ...\\n2. ...", "expected_result": "..."}
  ],
  "risks": [
    {"category": "third_party_integration", "title": "...", "description": "...",
     "likelihood": 3, "impact": 4, "mitigation": "..."}
  ],
  "dependencies": [
    {"kind": "internal", "title": "...", "description": "...", "depends_on_title": "...",
     "external_type": "", "owner_note": "", "due_offset_days": 7}
  ]
}""" % (
    MAX_CRITERIA, MAX_TESTS, MAX_RISKS, MAX_DEPS,
    FIELD_TYPES, MANDATORY, TEST_TYPES, RISK_CATEGORIES, DEPENDENCY_KINDS,
)


def requirement_messages(req, research, sources):
    import json as _json

    user = (
        f"REQUIREMENT\nTitle: {req['title']}\n"
        f"Functional area: {req.get('functional_area', '—')}\n"
        f"Priority: {req.get('priority', 3)} | Estimate: {req.get('estimate_points', 0)} pts\n"
        f"Draft user story: {req.get('user_story', '—')}\n\n"
        f"RESEARCH BRIEF\n{_json.dumps(research, indent=1)}\n\n"
        f"BUSINESS REQUIREMENT DOCUMENT\n{sources['brd'] or '(none)'}\n\n"
        f"DEVELOPMENT INSTRUCTIONS\n{sources['dev_instructions'] or '(none)'}\n\n"
        "Detail this requirement now."
    )
    return REQUIREMENT_SYSTEM, user


SERVICES_SYSTEM = """You are cataloguing the external / serviceable items a project needs \
from the client or third parties, for a "Serviceable Items / External Services" register.

From the Tech Stack, BRD and research brief, list every account, credential, gateway, \
licence, cloud resource, domain, external database, hosting/server access or \
client-provided datum the team will need. For each, say who provides it and link the \
requirements that need it by title.

Allowed category codes: %s. provided_by: %s. status: use "identified" unless the source \
clearly says it is already set up ("configured").

Return ONLY this JSON:
{
  "services": [
    {"category": "smtp", "name": "SMTP relay credentials", "description": "...",
     "provided_by": "client", "status": "identified", "requirement_titles": ["..."]}
  ]
}""" % (SERVICE_CATEGORIES, SERVICE_PROVIDERS)


def services_messages(sources, research, requirement_titles):
    import json as _json

    user = (
        f"TECH STACK\n{sources['tech_stack'] or '(none)'}\n\n"
        f"BUSINESS REQUIREMENT DOCUMENT\n{sources['brd'] or '(none)'}\n\n"
        f"RESEARCH BRIEF (external_services hint)\n{_json.dumps(research.get('external_services', []), indent=1)}\n\n"
        f"REQUIREMENTS IN THIS PLAN\n" + "\n".join(f"- {t}" for t in requirement_titles) + "\n\n"
        "Produce the external services list now."
    )
    return SERVICES_SYSTEM, user
