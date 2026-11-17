# Penpot `execute_code` recipes

Working patterns for building Planwise screens in Penpot. Adapt them — they are a starting
point that already handles the traps, not a template to paste unchanged.

Call `high_level_overview` once per conversation for the live API rules, and
`penpot_api_info(type, member)` for specific signatures rather than guessing.

## Contents

- [Ground rules](#ground-rules)
- [1. Bootstrap a helper library into `storage`](#1-bootstrap-a-helper-library-into-storage)
- [2. Find-or-create a screen board](#2-find-or-create-a-screen-board)
- [3. A form field from one field spec](#3-a-form-field-from-one-field-spec)
- [4. A whole form screen from field specs](#4-a-whole-form-screen-from-field-specs)
- [5. A data table](#5-a-data-table)
- [6. Verify before you claim it works](#6-verify-before-you-claim-it-works)
- [7. Extract code from the design](#7-extract-code-from-the-design)

## Ground rules

These cause most of the wasted cycles:

- **`width` / `height` are read-only.** Use `resize(w, h)`.
- **`resize()` forces `growType = "fixed"` on text.** Set it back to `"auto-height"` or the
  text clips. Auto-sizing is async — `await sleep(100)` before reading new bounds.
- **Fill and stroke arrays are read-only in place.** Replace the whole array:
  `shape.fills = [{ fillColor: "#FFFFFF", fillOpacity: 1 }]`. Mutating `fills[0]` does
  nothing.
- **Hex colours must be uppercase**: `#1D4ED8`, not `#1d4ed8`.
- **`fontSize` is a string** in the Penpot API: `text.fontSize = "14"`.
- **`penpot.createText()` can return `null`.** Null-check it.
- **A board with a layout controls its children's positions.** Setting `x`/`y` on a child
  does nothing unless `child.layoutChild.absolute` is true. Change gaps, padding and margins
  instead.
- **`board.addFlexLayout()` on a board that already has children reorders them.** Use
  `penpotUtils.addFlexLayout(container, dir)` to preserve visual order.
- **`parentX` / `parentY` are read-only.** Use `penpotUtils.setParentXY(shape, x, y)`.
- **Don't `console.log` what you also return** — you'd receive it twice.

## 1. Bootstrap a helper library into `storage`

`storage` persists across `execute_code` calls, so build the toolkit once. Everything below
assumes this has run.

```js
// execute_code — run once per session
storage.sleep = (ms) => new Promise(r => setTimeout(r, ms));

// Neutral defaults. Replace with the project's real palette when the spec or an
// existing design library provides one — see "when the spec is thin" in SKILL.md.
storage.T = {
  bg: "#FFFFFF", surface: "#F8FAFC", border: "#E2E8F0",
  text: "#0F172A", muted: "#64748B",
  primary: "#4F46E5", danger: "#DC2626",
  gap: 16, pad: 24, radius: 8, field: 40, font: "Work Sans",
};

storage.text = (chars, { size = "14", color = storage.T.text, weight } = {}) => {
  const t = penpot.createText(String(chars));
  if (!t) throw new Error("createText returned null: " + chars);
  t.growType = "auto-height";
  t.fontFamily = storage.T.font;
  t.fontSize = size;
  if (weight) t.fontWeight = weight;
  t.fills = [{ fillColor: color, fillOpacity: 1 }];
  return t;
};

storage.box = (name, w, h, { fill = storage.T.bg, border = storage.T.border } = {}) => {
  const r = penpot.createRectangle();
  r.name = name;
  r.resize(w, h);
  r.fills = [{ fillColor: fill, fillOpacity: 1 }];
  if (border) r.strokes = [{ strokeColor: border, strokeStyle: "solid", strokeWidth: 1, strokeAlignment: "inner" }];
  r.borderRadius = storage.T.radius;
  return r;
};

// column flex board — the workhorse container
storage.col = (name, { gap = storage.T.gap, pad = 0 } = {}) => {
  const b = penpot.createBoard();
  b.name = name;
  const f = b.addFlexLayout();
  f.dir = "column";
  f.rowGap = gap;
  f.verticalPadding = pad;
  f.horizontalPadding = pad;
  f.horizontalSizing = "auto";
  f.verticalSizing = "auto";
  return b;
};

return "helpers ready";
```

## 2. Find-or-create a screen board

The idempotence primitive. Every build should start here so a re-run edits the existing
board instead of stacking a duplicate on top of it.

```js
storage.screen = (name, width = 480) => {
  let b = penpotUtils.findShape(s => s.name === name && s.type === "board");
  if (b) return { board: b, created: false };

  b = storage.col(name, { gap: storage.T.gap, pad: storage.T.pad });
  b.fills = [{ fillColor: storage.T.bg, fillOpacity: 1 }];
  b.resize(width, 320);
  b.flex.horizontalSizing = "fix";
  b.flex.verticalSizing = "auto";

  // lay new screens out left-to-right so state variants sit side by side
  const siblings = penpotUtils.findShapes(
    s => s.type === "board" && s.parent && s.parent.id === penpot.root.id
  ).filter(s => s.id !== b.id);
  const right = siblings.reduce((m, s) => Math.max(m, s.x + s.width), 0);
  b.x = siblings.length ? right + 80 : 0;
  b.y = 0;
  return { board: b, created: true };
};

const { board, created } = storage.screen("PLAT-R7 · Add Participant — Default");
storage.board = board;
return { id: board.id, created };
```

To rebuild a screen's contents cleanly without moving it, clear the children first:

```js
[...storage.board.children].forEach(c => c.remove());
```

## 3. A form field from one field spec

One Planwise `field_specs` row in, one labelled control out — including the required marker,
helper text and optional error, so the states the criteria demand are drawable.

```js
storage.field = (spec, { width = 432, error = null } = {}) => {
  const wrap = storage.col(`Field: ${spec.name}`, { gap: 6 });
  wrap.fills = [];

  const required = spec.mandatory === "yes";
  const label = storage.text(spec.name + (required ? " *" : ""), { size: "13", weight: 500 });
  wrap.appendChild(label);

  // Long text and File need more room than a standard control
  const h = spec.type === "Long text" ? 96 : storage.T.field;
  const control = storage.box(`Input: ${spec.name}`, width, h, {
    fill: storage.T.surface,
    border: error ? storage.T.danger : storage.T.border,
  });
  wrap.appendChild(control);

  if (spec.default_value) {
    const val = storage.text(spec.default_value, { size: "14" });
    wrap.appendChild(val);
    val.name = `Value: ${spec.name}`;
  }

  // helper carries the contract: validation rule and length limit
  const hints = [spec.validation, spec.max_length ? `max ${spec.max_length}` : null]
    .filter(Boolean).join(" · ");
  if (error) wrap.appendChild(storage.text(error, { size: "12", color: storage.T.danger }));
  else if (hints) wrap.appendChild(storage.text(hints, { size: "12", color: storage.T.muted }));

  wrap.flex.horizontalSizing = "fix";
  wrap.resize(width, 10);
  wrap.flex.verticalSizing = "auto";
  return wrap;
};
return "field helper ready";
```

## 4. A whole form screen from field specs

Feed it the `field_specs` array straight out of `get_requirement`. Pass `errors` to render a
validation-state variant of the same screen.

```js
storage.formScreen = async (title, specs, { errors = {}, formError = null, name } = {}) => {
  const { board } = storage.screen(name || title);
  [...board.children].forEach(c => c.remove());

  board.appendChild(storage.text(title, { size: "20", weight: 600 }));

  if (formError) {
    const banner = storage.col("Form error", { gap: 0, pad: 12 });
    banner.fills = [{ fillColor: "#FEF2F2", fillOpacity: 1 }];
    banner.appendChild(storage.text(formError, { size: "13", color: storage.T.danger }));
    board.appendChild(banner);
    banner.flex.horizontalSizing = "fill";
  }

  for (const spec of specs) {
    board.appendChild(storage.field(spec, { error: errors[spec.name] || null }));
  }

  const submit = storage.box("Button: Submit", 120, storage.T.field, {
    fill: storage.T.primary, border: null,
  });
  board.appendChild(submit);

  await storage.sleep(150);            // let auto-height settle before measuring
  return { id: board.id, fields: specs.length };
};

// then, per state variant:
return await storage.formScreen("Add Participant", storage.specs, {
  name: "PLAT-R7 · Add Participant — Default",
});
```

Store the spec array once so later calls don't refetch:
`storage.specs = [ ...field_specs from get_requirement... ]`.

## 5. A data table

For list and search requirements. Header row plus sample rows, built from column names.

```js
storage.table = (name, columns, rows, { width = 720 } = {}) => {
  const { board } = storage.screen(name, width);
  [...board.children].forEach(c => c.remove());
  board.flex.rowGap = 0;

  const colW = Math.floor((width - storage.T.pad * 2) / columns.length);

  const mkRow = (cells, { header = false } = {}) => {
    const r = penpot.createBoard();
    r.name = header ? "Header" : "Row";
    const f = r.addFlexLayout();
    f.dir = "row";
    f.columnGap = 0;
    f.verticalPadding = 12;
    f.horizontalSizing = "fill";
    f.verticalSizing = "auto";
    r.fills = [{ fillColor: header ? storage.T.surface : storage.T.bg, fillOpacity: 1 }];
    cells.forEach(c => {
      const t = storage.text(c, {
        size: header ? "12" : "14",
        color: header ? storage.T.muted : storage.T.text,
        weight: header ? 600 : undefined,
      });
      r.appendChild(t);
      t.layoutChild.horizontalSizing = "fill";
      t.resize(colW, 20);
      t.growType = "auto-height";
    });
    return r;
  };

  board.appendChild(mkRow(columns, { header: true }));
  rows.forEach(row => board.appendChild(mkRow(row)));
  return { id: board.id, rows: rows.length };
};
return "table helper ready";
```

Every table needs an empty-state variant too — a board with the header and a single muted
"No participants yet" line. Acceptance criteria almost always imply one even when they
don't say so.

## 6. Verify before you claim it works

Structure check, then an actual render. The object tree can look perfect while the screen is
visibly broken.

```js
// containment: anything escaping its board is a layout bug
const board = storage.board;
const escapees = penpotUtils.analyzeDescendants(board, (root, s) =>
  penpotUtils.isContainedIn(s, root) ? null : { name: s.name, x: s.x, y: s.y }
);
return {
  structure: penpotUtils.shapeStructure(board, 3),
  bounds: { w: board.width, h: board.height },
  escapees: escapees.map(e => e.result),
};
```

Then look at it:

```
export_shape(shapeId="<board id>", format="png")
```

Use `shapeId: "page"` to see all state variants at once — the fastest way to spot
inconsistent spacing between boards. Then read the acceptance criteria one at a time and
name which board satisfies each.

## 7. Extract code from the design

The design is the source of truth for styling. Extract it rather than re-deriving values by
eye, then translate into the project's real stack from `tech_stack` / `dev_instructions`.

```js
const shapes = [storage.board];
return {
  markup: penpot.generateMarkup(shapes, { type: "html" }),
  css: penpot.generateStyle(shapes, { type: "css" }),
};
```

Keep the extracted values exact. When you convert to Angular + PrimeNG (or whatever the
project uses), carry the `field_specs` constraints into the real validators —
`mandatory: "yes"` → required, `max_length` → maxlength, `validation` → pattern — so the
spec, the design and the code all agree.
