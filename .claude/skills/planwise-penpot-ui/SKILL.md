---
name: planwise-penpot-ui
description: >-
  Design and build a project's UI by working the Planwise MCP server (requirements,
  field specs, acceptance criteria, tickets, BRD, tech stack, dev log) together with the
  Penpot MCP server (creating and editing the actual design file through execute_code).
  Use this whenever the work touches screens, forms, mockups, wireframes, or components
  for a project tracked in Planwise — "design the UI for PLAT-R7", "build the screens for
  this sprint", "turn this requirement into a mockup", "what should the Add Participant
  form look like", "generate the Angular components from the Penpot design", "review the
  design against the acceptance criteria". Reach for it even when only one side is named:
  a mockup request for a feature that has a Planwise requirement still needs the spec, and
  a "build this screen" request still needs the design. Also use it for the setup question
  "how do I connect Claude Code to Penpot for this project".
---

# Planwise → Penpot → code

Planwise holds the **contract**: what the screen must contain, which fields, which
validations, what "done" means. Penpot is where that contract becomes something a person
can look at. Your job is to carry work across that bridge without inventing anything on
either side, then report back so the project record stays true.

The failure mode this skill exists to prevent is a beautiful mockup that quietly disagrees
with the spec — wrong fields, invented copy, missing validation states — which nobody
notices until a developer builds it.

## Two servers, two jobs

| | Planwise MCP | Penpot MCP |
|---|---|---|
| Gives you | requirements, field specs, acceptance criteria, tickets, BRD, tech stack, dev log | one connected design file |
| Key tools | `get_requirement`, `get_document`, `ask_project`, `push_dev_update` | `execute_code`, `export_shape` |
| Scope | exactly one project (the token decides) | exactly one open file |
| Writes | only if the link has write scope | always |

Tool names may appear prefixed depending on how each server was added
(`mcp__planwise-plat__get_requirement`). Match on the suffix.

Full Planwise tool catalogue: `references/planwise-tools.md` — read it when you need a tool
you don't see below, or when you're unsure which of `search_docs` / `ask_project` /
`query_data` fits the question.

## Preflight

Penpot's MCP is **not headless**. It drives a browser plugin, so a person must have the
design file open with the plugin connected before any `execute_code` call will work. When
Penpot tools hang or error, this is almost always why.

Probe it cheaply before planning any design work:

```js
// execute_code
return {
  file: penpot.currentFile?.name,      // Planwise names these "<KEY> — UI"
  page: penpot.currentPage?.name,
  version: penpot.version,
};
```

The Penpot MCP is a **shared server on the Penpot host** (not something you install locally):
the project's **Design** tab shows the exact `.mcp.json` — `planwise` and `penpot` entries,
the `penpot` URL is `http://<penpot-host>:9001/mcp/stream`. Nothing to run with `npx`.

If the `execute_code` probe fails, stop and hand the setup back to the user — you cannot fix
it from here:

1. `.mcp.json` has **both** servers (`planwise` for context, `penpot` for the canvas) —
   copy it from the project's Planwise **Design** tab; restart Claude Code after editing.
2. Planwise → the project → **Design** tab → **Open in Penpot (edit)**
   (if the project has no workspace yet, that tab provisions one)
3. In Penpot: **Plugins → Connect to MCP server** (built in — no manifest URL to paste)
4. Leave the plugin panel open; closing it drops the connection

A failed probe is almost always step 3 (no file open / plugin not connected) or step 1
(the `penpot` server missing from `.mcp.json`).

**Check the two servers agree on which project this is.** The Planwise token is bound to one
project and the Penpot plugin is connected to one file, and nothing enforces that they
match. Compare the file name against `project_overview`'s `key` — building PLAT's
requirements into MOB's design file is silent, plausible-looking, and expensive to unpick.
If they disagree, say so and stop.

If you have never read Penpot's own **High-Level Overview** in this conversation, call
`high_level_overview` once — it carries the live API rules. Use `penpot_api_info` for
specific types rather than guessing member names.

## The loop

### 1. Ground yourself before designing anything

```
project_overview                      → what this project is, what exists
get_document(kind="tech_stack")       → what you are designing FOR
get_document(kind="dev_instructions") → the house rules you must obey
```

The tech stack is not background reading — it constrains the design. If the stack says
Angular + PrimeNG, design components PrimeNG can actually express and later emit Angular,
not React with Tailwind. Defaulting to your favourite stack here produces a design the team
cannot build.

Pull the BRD (`get_document(kind="brd")`) or `search_docs` when you need the *why* behind a
requirement — a decision from a client meeting often explains a field that looks arbitrary.

### 2. Take the spec, and treat it as the design brief

```
list_requirements(sprint="PLAT-S2")   → what's in play
get_requirement(number="PLAT-R7")     → the contract for one screen
```

`get_requirement` returns the three things that decide the design:

- **`field_specs`** — every form control, with `type`, `mandatory`, `max_length`,
  `validation`, `default_value`. This is your field list. Not a suggestion, not a starting
  point. If it lists eight fields, the design has eight fields.
- **`acceptance_criteria`** — the states the screen must support. A criterion like *"an
  error is shown when the email is already registered"* means the design owes an error
  state, not just a happy path.
- **`description` / `user_story`** — the intent, useful for layout and hierarchy decisions
  the spec doesn't pin down.

The mapping from field type to UI control, and the checklist of states criteria imply, is
in `references/field-spec-to-ui.md`. Read it before laying out any form.

For bug work, `get_ticket` gives reproduction steps and expected-vs-actual — the expected
result is the design target.

### 3. Design in Penpot

Build with `execute_code`. Reusable, idempotent recipes — find-or-create boards, form
scaffolds, tables, page shells — are in `references/penpot-recipes.md`. Use them instead of
writing layout code from scratch; they already handle the traps below.

Three habits that matter more than anything else:

**Name everything after the spec.** A board called `PLAT-R7 · Add Participant` and fields
called `Field: Email` are how the design stays traceable — anyone can look at a board and
find its requirement, and you can find your own work again next session:

```js
storage.board = penpotUtils.findShape(s => s.name.startsWith("PLAT-R7"));
```

Untitled boards full of `Rectangle 47` are unmaintainable and make re-runs duplicate work.

**Make every build idempotent.** Penpot has no branches and no undo you control. Always
find-or-create rather than blindly create, or the second run leaves two overlapping copies
of the same screen.

**Build incrementally and keep state in `storage`.** `storage` persists across
`execute_code` calls, so stash the board, helper functions, and the palette once rather
than rebuilding them each call.

### 4. Look at what you made

```
export_shape(shapeId=<board id>, format="png")
```

Do this before claiming anything is done. Layout bugs — text overflowing its box, children
escaping the board, a flex layout silently reordering everything — are invisible in the
object tree and obvious in the render. Designers check their work by looking at it; the
tooling gives you the same ability, so use it.

Two specific traps worth knowing before you render:

- `resize()` sets a text's `growType` to `"fixed"`. Set it back to `"auto-height"` or the
  text clips. Auto-sizing is async — sleep ~100 ms before reading the new bounds.
- Adding a flex layout to a board that *already* has children reorders them by child index.
  Use `penpotUtils.addFlexLayout(container, dir)`, which preserves visual order.

Then check the render against the acceptance criteria one at a time. This is the moment the
skill pays for itself — read each criterion and point at where the design satisfies it.

### 5. Generate code from the design

The design is the source of truth for styling, so extract rather than re-derive:

```js
const shapes = [storage.board];
return {
  markup: penpot.generateMarkup(shapes, { type: "html" }),
  css: penpot.generateStyle(shapes, { type: "css" }),
};
```

Translate that into the project's real stack (from `tech_stack` / `dev_instructions`) —
Angular components, PrimeNG controls, whatever the project actually uses. Keep the values
exact. Inventing a spacing scale or a slightly nicer blue than the design uses is how the
built screen drifts from the approved mockup.

Wire the validation from `field_specs` into the real form controls: `max_length` becomes a
maxlength validator, `validation` becomes the pattern, `mandatory: "yes"` becomes required.
The spec, the design and the code should agree on all three.

### 6. Report back into Planwise

Work that isn't recorded didn't happen, as far as the project is concerned.

```
list_dev_milestones                   → reuse an existing milestone label
push_dev_update(headline=..., kind="feature", requirement="PLAT-R7",
                milestone=..., details=...)
```

Reuse milestone labels rather than inventing near-duplicates — the rollup on the Dev Log tab
groups on the exact string. Link the requirement or ticket so the update lands on the right
record.

If the link has write scope, close the loop properly:
`set_requirement_status(number="PLAT-R7", status="in_review")`,
`record_test_result(...)` when you have verified a test case. Without write scope these fail
with a clear message — report that to the user rather than retrying.

Put the Penpot file or board URL in the update's `details` so a reviewer can jump straight
to what you built.

## When the spec is thin

Real requirements have gaps. Do not fill them silently with plausible-looking design.

- Missing a value the design needs (brand colour, spacing scale, icon set)? Use a neutral
  default, and say plainly which values you chose and why, so a designer can correct them.
- Missing a whole behaviour (no criterion covers the empty state)? Design the obvious thing,
  flag it, and offer to `create_ticket` or `add_acceptance_criterion` so the gap becomes
  visible in the project rather than living only in the mockup.
- Contradiction between spec and BRD? Surface it. Do not pick a winner on your own — that's
  a product decision and someone else owns it.

The general principle: your uncertainty should end up in the project record, not hidden
inside a design file.

## Reference files

- `references/planwise-tools.md` — every Planwise MCP tool, grouped by what you're trying to
  learn, with the write scopes each one needs
- `references/field-spec-to-ui.md` — field type → control mapping, the states acceptance
  criteria imply, and how to read a spec table as a form
- `references/penpot-recipes.md` — idempotent `execute_code` recipes: find-or-create boards,
  form scaffold from field specs, data table, page shell, verification helpers
