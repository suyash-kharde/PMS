"""The combined workbook has one tab per kind. Customers are created before
People before Projects so a Project row can reference a brand-new Customer or
a brand-new Owner defined earlier in the very same file — plain name/username
lookups otherwise only see what's already in the database.

For a preview (dry_run=True) we run the *real* create calls inside a transaction
and then roll the whole thing back, instead of maintaining a second, parallel
"pretend" resolution path. That guarantees the preview can never drift from
what commit actually does, at the cost of touching the database twice for a
confirmed import (once to preview, once for real) — cheap for the row counts
this feature is meant for.
"""

from django.db import transaction
from openpyxl import load_workbook

from .commit import COMMITTERS
from .specs import KIND_ORDER, LABELS
from .validate import find_sheet, validate_ws


class _Rollback(Exception):
    pass


def run_combined(fileobj, dry_run):
    fileobj.seek(0)
    try:
        wb = load_workbook(fileobj, data_only=True, read_only=True)
    except Exception as exc:
        return {"file_error": f"Could not read this file: {exc}", "sections": {}, "created": {}, "counts": {}}

    sections, created = {}, {}
    try:
        with transaction.atomic():
            for kind in KIND_ORDER:
                ws = find_sheet(wb, kind)
                if ws is None:
                    sections[kind] = {
                        "missing_sheet": True, "file_error": None,
                        "rows": [], "missing_optional": [], "counts": {"total": 0, "create": 0, "error": 0, "warning": 0},
                    }
                    created[kind] = []
                    continue

                result = validate_ws(kind, ws)
                result["missing_sheet"] = False
                sections[kind] = result
                if result["file_error"]:
                    created[kind] = []
                    continue

                created[kind] = COMMITTERS[kind](result["rows"])
            if dry_run:
                raise _Rollback()
    except _Rollback:
        pass

    if all(sections[k]["missing_sheet"] for k in KIND_ORDER):
        return {
            "file_error": "No “Customers”, “People” or “Projects” sheet found in this file.",
            "sections": {}, "created": {}, "counts": {},
        }

    counts = {
        "total": sum(sections[k]["counts"]["total"] for k in KIND_ORDER),
        "create": sum(sections[k]["counts"]["create"] for k in KIND_ORDER),
        "error": sum(sections[k]["counts"]["error"] for k in KIND_ORDER),
        "warning": sum(sections[k]["counts"]["warning"] for k in KIND_ORDER),
    }
    return {"file_error": None, "sections": sections, "created": created, "counts": counts, "labels": LABELS}
