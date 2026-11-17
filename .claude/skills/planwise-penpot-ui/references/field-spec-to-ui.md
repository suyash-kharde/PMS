# Reading a Planwise spec as a UI

The purpose of this file is to make the translation from spec to screen mechanical, so the
design can't quietly disagree with the contract.

## The rule

**The field spec table is the field list.** Every row is one control, in order. Don't add a
field because it feels natural, don't drop one because it looks redundant. If the spec is
wrong, that's a conversation with the requirement's owner — `add_acceptance_criterion` or
`create_ticket` — not a silent edit in the design.

## Field type → control

`field_specs[].type` comes back as a display label. Map it to the control the project's
component library actually offers (check `tech_stack` — PrimeNG, Material and plain HTML
name these differently).

| Spec type | Control | Design notes |
|---|---|---|
| Text | single-line input | width should suit `max_length`, not the full form width |
| Long text | textarea | 3–5 rows visible; show a character counter if `max_length` is set |
| Email | email input | needs an invalid-format error state |
| Number | numeric input / spinner | show unit and range if `validation` mentions one |
| Phone | phone input | country prefix if `validation` implies international |
| Date | date picker | show the expected format in the placeholder or helper |
| Date & time | datetime picker | say which timezone if it matters |
| Yes / No | checkbox, or radio pair | radios when both options need to be visible and equal |
| Choice | select / dropdown | ≤5 static options reads better as radios; show the option list if `notes` has it |
| File | file upload | show accepted types and size limit from `validation` / `notes` |

## The other columns drive states, not just controls

| Column | What the design owes |
|---|---|
| `mandatory: "yes"` | a required marker, and a "this field is required" error state |
| `mandatory: "depends"` | read `mandatory_condition` — the field is conditionally required, so the design needs both variants, or at least a note on the board |
| `max_length` | counter or truncation behaviour, plus the over-length error |
| `validation` | the format hint (helper text) **and** the invalid-format error |
| `default_value` | show it pre-filled in the default state, not as placeholder text |
| `notes` | often carries the option list, the copy, or the edge case — read it |

Placeholder text is not a label and not a default. If a field has a `default_value`, the
control shows that value; if it has `validation`, that belongs in persistent helper text,
because placeholders vanish the moment someone types.

## Acceptance criteria → states to draw

Read every criterion and ask "what would I have to see on screen to know this is true?"
Criteria in given/when/then form are especially direct — the *then* is usually a visible
state.

A screen typically owes some subset of:

- **Default** — empty or pre-filled with defaults
- **Filled / valid** — the happy path
- **Field-level error** — one per validation rule that can fail
- **Form-level error** — server rejection, duplicate record, permission denied
- **Loading / submitting** — if the criterion mentions saving or fetching
- **Success** — confirmation, redirect target, or the updated list
- **Empty** — for any list or table with no rows
- **Disabled / read-only** — if any criterion mentions permissions or a locked record

You don't have to draw all of these for every screen. Draw the ones the criteria actually
imply, and lay them out as sibling boards so a reviewer can see them side by side.

`list_test_cases(requirement=...)` is a second source for this — the `negative`,
`validation` and `boundary` cases enumerate failure modes someone has already thought
through.

## Worked example

Spec for `PLAT-R7 · Add Participant`:

| name | type | mandatory | max_length | validation | default |
|---|---|---|---|---|---|
| First Name | Text | yes | 50 | Alphabetic | |
| Email | Email | yes | 120 | Valid email format | |
| Role | Choice | yes | | One of: Owner, Editor, Viewer | Viewer |
| Allocation % | Number | no | | 0–100 | 100 |
| Remarks | Long text | no | 500 | | |

Criteria include *"the form cannot be submitted with an empty required field"* and *"an
error is shown when the email already belongs to a participant on this project"*.

That produces five controls in that order, and at least four boards:

1. **Default** — Role preselected to Viewer, Allocation showing 100
2. **Validation errors** — required messages on First Name and Email, "letters only" on
   First Name, character counter on Remarks near its limit
3. **Duplicate email** — form-level error, from the second criterion
4. **Success** — whatever the criteria say happens after save

Names: board `PLAT-R7 · Add Participant — Default`, fields `Field: First Name`,
`Field: Email`, and so on. The requirement key in every board name is what keeps the design
traceable back to the spec.

## Before you call it done

Walk the criteria list and say, for each one, which board shows it. If a criterion has no
board, either draw it or flag it. That check is the whole point of working from the spec —
skipping it puts you back to designing from vibes, with extra steps.
