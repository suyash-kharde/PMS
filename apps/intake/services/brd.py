"""BRD authoring: build the prompt, call the LLM, store a new immutable version."""

import re

from django.utils import timezone

from apps.projects.models import ProjectMembership

from ..models import BRD, TimelineEntry
from . import llm

SYSTEM_PROMPT = """You are a senior business analyst who maintains Business Requirement \
Documents (BRD). You receive the current BRD, the project's people (client side and \
delivery side, with their roles, responsibilities and remarks) and new input from the \
project timeline — typed notes, uploaded documents or speaker-attributed meeting \
transcripts.

Rules:
- Rewrite and return the COMPLETE updated BRD in GitHub-flavoured Markdown. Never return
  a fragment, a patch or a diff.
- Preserve everything from the current BRD that the new input does not change. Do not
  drop earlier requirements silently; if something is superseded, say so explicitly.
- Attribute requirements to the person who raised them, by name, e.g.
  "(raised by Priya Nair, Client — Product Owner)". Use the real names supplied in the
  speaker map; never emit raw diarizer labels such as SPEAKER_00.
- Be concrete. No filler, no restating the instructions, no apologies.
- Mark anything ambiguous under "Open Questions" instead of inventing detail.

Two different kinds of timeline input need different handling — check each entry's kind
and treat it accordingly:
- Meeting notes / transcripts (kind: Meeting, Note, Decision, Change request with spoken
  content): extract requirements from what was said, attribute each to the person who said
  it as above.
- A formal already-structured source document (kind: Text / document — a specification,
  problem statement, RFP, or similar uploaded file with no speaker): it has no individual
  author to attribute to. Attribute its requirements to the document itself, e.g.
  "(from 01_Problem_Statement.pdf)", never to a fabricated person. Preserve the document's
  own substance fully — every problem, requirement, role, and business rule it states must
  land in the matching BRD section — but you may freely reorganise, rephrase, and split it
  into the BRD's structure and FR-IDs; you are not limited to fixing wording.
- A source document may itself describe the system's future USER ROLES/personas (e.g.
  "ADMIN", "VENDOR", "INSPECTOR" as roles the finished application will support). Those are
  functional requirements about who will use the system — they belong under Functional
  Requirements (and may be summarised in the Executive Summary/Scope), never in the
  Stakeholders table. Do not merge a system role from a document with a person on the
  delivery roster just because the names are similar.

Stakeholders (section 4) — the project roster under "PEOPLE ON THIS PROJECT" is the single
source of truth for WHO IS DELIVERING this project, not who will use the finished system:
- Reproduce that roster table as-is. It already carries every person's side, role,
  responsibility and remarks; do not restate it in prose alongside the table.
- Exactly one row per person. A person named in the speaker map is the SAME person as the
  roster row with that name, not an additional stakeholder — never give anyone a second row
  because a meeting revealed more about them.
- Never invent a stakeholder, a side or a job title that the roster does not state. If the
  timeline suggests a different role or side than the roster records, keep the roster value
  and raise the discrepancy under "Open Questions".
- People mentioned in a transcript who are not on the roster do not belong in section 4;
  list them under "Open Questions" so they can be added to the project properly.
- End-user roles/personas defined by a requirements document (see above) never belong in
  section 4 either, even if the role name looks like a job title — they are product scope,
  not delivery-team stakeholders.

Keep this exact section order:
# <Project> — Business Requirement Document
## 1. Document Control
## 2. Executive Summary
## 3. Background and Business Context
## 4. Stakeholders
## 5. Scope
### 5.1 In Scope
### 5.2 Out of Scope
## 6. Functional Requirements
## 7. Non-Functional Requirements
## 8. Assumptions, Constraints and Dependencies
## 9. Risks
## 10. Open Questions
## 11. Decision Log
## 12. Change History

Functional requirements use stable IDs (FR-001, FR-002 …). Reuse an existing ID when you
revise that requirement; only allocate a new ID for genuinely new scope.

Answer in exactly this envelope and nothing else:
<<<SUMMARY>>>
one or two sentences naming what changed in this revision
<<<BRD>>>
the complete markdown document
"""


STAKEHOLDER_COLUMNS = ["Name", "Organisation", "Role", "Responsibility", "Remarks"]


def stakeholder_block(project) -> str:
    """The roster, already shaped as the section 4 table so the model copies it verbatim.

    One row per membership, deduplicated by person: emitting the same name twice here is
    what used to make the model publish a stakeholder twice in the BRD.
    """
    rows = ProjectMembership.objects.filter(project=project).select_related("user", "role")
    merged = {}
    for member in rows:
        name = (member.user.display_name or "").strip()
        key = normalise_name(name)
        if not key:
            continue
        role = member.role_description or member.position or (member.role.name if member.role_id else "")
        cells = [
            name,
            member.get_side_display(),
            role or "—",
            member.responsibility or "—",
            member.remarks or "—",
        ]
        if key in merged:
            merged[key] = merge_cells(merged[key], cells)
        else:
            merged[key] = cells
    if not merged:
        return "No stakeholders recorded yet."
    return render_table(STAKEHOLDER_COLUMNS, list(merged.values()))


def render_table(columns, rows) -> str:
    head = "| " + " | ".join(columns) + " |"
    rule = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows]
    return "\n".join([head, rule, *body])


def speaker_block(entry: TimelineEntry) -> str:
    speakers = list(entry.speakers.all())
    if not speakers:
        return ""
    lines = []
    for speaker in speakers:
        member = None
        if speaker.user_id:
            member = ProjectMembership.objects.filter(project=entry.project, user_id=speaker.user_id).first()
        lines.append(
            f"- {speaker.label} = {speaker.resolved_name}"
            + (f" — already on the roster above as {speaker.resolved_name} "
               f"({member.get_side_display()}{', ' + member.position if member.position else ''}); "
               f"do not add a second stakeholder row for this voice" if member else
               (f" ({speaker.get_side_display()}, not on the project roster)" if speaker.side
                else " (unmapped to a project member)"))
        )
    return "Speaker map for this input:\n" + "\n".join(lines)


def build_messages(project, current_markdown: str, entries) -> list:
    inputs = []
    for entry in entries:
        header = (
            f"### Timeline entry #{entry.pk} — {entry.get_kind_display()} — "
            f"{entry.occurred_at:%Y-%m-%d %H:%M} — logged by "
            f"{entry.author.display_name if entry.author_id else 'system'}\n"
            f"Title: {entry.title}"
        )
        speakers = speaker_block(entry)
        # set by regenerate() when the raw transcript will not fit the context window
        body = getattr(entry, "condensed_note", None)
        if body:
            body = f"(condensed from the full transcript)\n{body}"
        else:
            body = entry.content_for_llm()
        inputs.append("\n".join(part for part in [header, speakers, body] if part))

    user_prompt = f"""PROJECT
{project.key} — {project.name}
Status: {project.get_status_display()} | Priority: {project.get_priority_display()}
Owner: {project.owner.display_name if project.owner_id else '—'}
Dates: {project.start_date or '—'} to {project.due_date or '—'}
Description: {project.description or '—'}

PEOPLE ON THIS PROJECT (complete roster — this is section 4 verbatim, one row per person)
{stakeholder_block(project)}

CURRENT BRD
{current_markdown or '(none yet — this is the first revision, create the document from scratch)'}

NEW TIMELINE INPUT
{chr(10).join(inputs) if inputs else '(no new input — restructure and tighten the existing document only)'}

Today is {timezone.localdate():%Y-%m-%d}. Produce the updated BRD."""

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


CONDENSE_PROMPT = """You are taking notes from part of a project meeting. Pull out only what
matters for a requirements document: decisions, requested features, constraints, numbers,
dates, risks and open questions. Keep the speaker's name against each point. Drop small
talk. Answer as terse bullet points in English, whatever language the transcript is in.
No preamble."""


def estimate_tokens(text: str) -> int:
    """Rough token count. Latin text runs ~4 chars per token; Devanagari and other
    non-Latin scripts are far denser, so count those characters much more heavily."""
    if not text:
        return 0
    non_latin = sum(1 for ch in text if ord(ch) > 0x7F)
    latin = len(text) - non_latin
    return int(latin / 4 + non_latin / 1.2) + 1


def chunk_text(text: str, max_tokens: int):
    """Split on line boundaries so a transcript turn is never cut in half."""
    chunks, current, size = [], [], 0
    for line in text.splitlines():
        line_tokens = estimate_tokens(line)
        if current and size + line_tokens > max_tokens:
            chunks.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += line_tokens
    if current:
        chunks.append("\n".join(current))
    return chunks


def condense(text: str, config, label=""):
    """Summarise a long input into requirement bullets so it fits the context window."""
    budget = max(1000, int(config.get("context_tokens", 16000) * 0.45))
    pieces = chunk_text(text, budget)
    notes = []
    for index, piece in enumerate(pieces, start=1):
        header = f"{label} (part {index} of {len(pieces)})" if len(pieces) > 1 else label
        reply, _meta = llm.chat(
            [
                {"role": "system", "content": CONDENSE_PROMPT},
                {"role": "user", "content": f"{header}\n\n{piece}"},
            ],
            max_tokens=min(900, config.get("max_tokens") or 900),
            temperature=0.1,
        )
        notes.append(reply.strip())
    return "\n".join(notes)


STAKEHOLDER_HEADING = re.compile(r"^(#{1,4})\s*\d*\.?\s*stakeholders?\b.*$", re.I)
EMPTY_CELL = {"", "-", "—", "–", "n/a", "na", "tbd", "none", "not specified", "unknown"}


def normalise_name(name: str) -> str:
    """Fold a display name to a comparison key: markdown, titles and spacing removed."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", name or "")  # links -> label
    text = re.sub(r"[*_`]", "", text)                            # bold/italic/code
    text = re.sub(r"\((?:[^)]*)\)", " ", text)                    # trailing "(Client)" etc.
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip().casefold()


def is_empty_cell(value: str) -> bool:
    return normalise_name(value) in EMPTY_CELL or not value.strip()


def merge_cells(keep, extra):
    """Combine two rows for the same person: richest non-empty value wins per column."""
    width = max(len(keep), len(extra))
    keep = list(keep) + [""] * (width - len(keep))
    extra = list(extra) + [""] * (width - len(extra))
    merged = []
    for first, second in zip(keep, extra):
        if is_empty_cell(first):
            merged.append(second)
        elif is_empty_cell(second) or normalise_name(first) == normalise_name(second):
            merged.append(first)
        else:
            merged.append(first if len(first) >= len(second) else second)
    return merged


def split_row(line: str):
    body = line.strip().strip("|")
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", body)]


def is_separator_row(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?[\s:|-]*-[\s:|-]*\|?\s*", line)) and "-" in line


def dedupe_table(lines):
    """Collapse repeated people in one markdown table, keyed on the first column."""
    header, separator, data = lines[0], lines[1], lines[2:]
    merged, order = {}, []
    for line in data:
        cells = split_row(line)
        key = normalise_name(cells[0] if cells else "")
        if not key:
            continue
        if key in merged:
            merged[key] = merge_cells(merged[key], cells)
        else:
            merged[key] = cells
            order.append(key)
    rows = ["| " + " | ".join(merged[key]) + " |" for key in order]
    return [header, separator, *rows]


def dedupe_bullets(lines):
    """Same idea for a bullet list: one bullet per person, keyed on the leading name."""
    seen, out = set(), []
    for line in lines:
        name = re.sub(r"^\s*[-*+]\s*", "", line)
        name = re.split(r"[—–:|(]", name, maxsplit=1)[0]
        key = normalise_name(name)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(line)
    return out


def dedupe_stakeholders(markdown: str) -> str:
    """Guarantee one entry per person in the Stakeholders section.

    The prompt already tells the model this, but a model that re-derives people from a
    transcript will still list someone twice — once from the roster, once from what the
    meeting revealed. This makes the rule hold whatever comes back.
    """
    lines = markdown.splitlines()
    start = next((i for i, line in enumerate(lines) if STAKEHOLDER_HEADING.match(line)), None)
    if start is None:
        return markdown
    level = len(STAKEHOLDER_HEADING.match(lines[start]).group(1))
    end = len(lines)
    for index in range(start + 1, len(lines)):
        heading = re.match(r"^(#{1,6})\s", lines[index])
        if heading and len(heading.group(1)) <= level:
            end = index
            break

    section, out, index = lines[start:end], [], 0
    while index < len(section):
        line = section[index]
        if line.strip().startswith("|") and index + 1 < len(section) and is_separator_row(section[index + 1]):
            stop = index + 2
            while stop < len(section) and section[stop].strip().startswith("|"):
                stop += 1
            out.extend(dedupe_table(section[index:stop]))
            index = stop
            continue
        if re.match(r"^\s*[-*+]\s+\S", line):
            stop = index
            while stop < len(section) and re.match(r"^\s*[-*+]\s+\S", section[stop]):
                stop += 1
            out.extend(dedupe_bullets(section[index:stop]))
            index = stop
            continue
        out.append(line)
        index += 1

    return "\n".join(lines[:start] + out + lines[end:])


def parse_response(text: str):
    """-> (summary, markdown). Tolerates a missing or partial envelope."""
    summary, markdown = "", text.strip()
    if "<<<BRD>>>" in text:
        head, _, body = text.partition("<<<BRD>>>")
        markdown = body.strip()
        summary = head.replace("<<<SUMMARY>>>", "").strip()
    if markdown.startswith("```"):
        markdown = markdown.split("\n", 1)[-1]
        if markdown.rstrip().endswith("```"):
            markdown = markdown.rstrip()[:-3].rstrip()
    return summary[:2000], dedupe_stakeholders(markdown)


def regenerate(project, entries=None, user=None, note=""):
    """Fold pending timeline entries into a new BRD version. Returns the BRDVersion."""
    brd, _ = BRD.objects.get_or_create(project=project, defaults={"title": f"{project.name} BRD"})
    if entries is None:
        entries = list(
            project.timeline.filter(include_in_brd=True, brd_applied=False)
            .exclude(status__in=["draft", "failed", "processing", "queued"])
            .order_by("occurred_at")
        )
    entries = list(entries)

    # Fit the request to the model's window: the reply has to hold a whole BRD, so the
    # prompt gets what is left. A long recording is condensed to bullets first rather than
    # truncated, which would silently drop requirements.
    from apps.aimodels.runtime import config_for

    config = config_for("llm")
    context = int(config.get("context_tokens") or 16000)
    reply_budget = min(int(config.get("max_tokens") or 8000), max(2000, int(context * 0.35)))
    prompt_budget = context - reply_budget - 400  # margin for the chat template

    messages = build_messages(project, brd.markdown, entries)
    prompt_tokens = sum(estimate_tokens(m["content"]) for m in messages)
    condensed = []

    if prompt_tokens > prompt_budget and entries:
        for entry in entries:
            body = entry.content_for_llm()
            if estimate_tokens(body) < 600:
                continue
            entry.condensed_note = condense(body, config, label=f"Timeline entry: {entry.title}")
            condensed.append(entry.pk)
        messages = build_messages(project, brd.markdown, entries)
        prompt_tokens = sum(estimate_tokens(m["content"]) for m in messages)

    reply_budget = max(1200, min(reply_budget, context - prompt_tokens - 400))
    text, meta = llm.chat(messages, max_tokens=reply_budget)
    summary, markdown = parse_response(text)
    if not markdown:
        raise llm.LLMError("the model returned an empty document")

    version = brd.add_version(
        markdown=markdown,
        summary=note or summary or "Updated from the project timeline",
        source_entry=entries[-1] if entries else None,
        created_by=user,
        model_name=meta.get("model", ""),
        meta={
            "usage": meta.get("usage", {}),
            "endpoint": meta.get("endpoint", ""),
            "entries": [e.pk for e in entries],
            "entry_titles": [e.title for e in entries],
            "condensed_entries": condensed,
            "prompt_tokens_estimated": prompt_tokens,
            "reply_budget": reply_budget,
            "speakers": sorted(
                {s.resolved_name for e in entries for s in e.speakers.all() if s.is_named}
            ),
        },
    )
    if entries:
        TimelineEntry.objects.filter(pk__in=[e.pk for e in entries]).update(brd_applied=True)

    # every path that produces a version notifies: worker, Sync button, Regenerate button
    from apps.notify import services as notifications

    notifications.brd_updated(version)
    return version
