"""Parses an uploaded sheet and checks every row against the current database.
Pure and read-only: nothing is written here. Called fresh both for the review
page and again right before commit, so what gets saved always matches what a
reviewer approved and reflects the latest state of the database.
"""

import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.utils.text import slugify
from openpyxl import load_workbook

from apps.accounts.models import Role, Team, User
from apps.projects.models import PRIORITIES, PROJECT_STATUS, Customer, Project

from .specs import COLUMNS


def _s(v):
    if v is None:
        return ""
    return str(v).strip()


def _bool(v, default=True):
    s = _s(v).lower()
    if s == "":
        return default, None
    if s in ("true", "1", "yes", "y"):
        return True, None
    if s in ("false", "0", "no", "n"):
        return False, None
    return default, f"Unrecognized value “{v}”, defaulted to {'TRUE' if default else 'FALSE'}"


def _date(v):
    if v is None or _s(v) == "":
        return None, None
    if isinstance(v, datetime.datetime):
        return v.date(), None
    if isinstance(v, datetime.date):
        return v, None
    s = _s(v)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date(), None
        except ValueError:
            continue
    return None, f"Could not parse date “{v}”"


def _decimal(v):
    if v is None or _s(v) == "":
        return Decimal("0"), None
    try:
        return Decimal(str(v)), None
    except (InvalidOperation, ValueError):
        return None, f"Could not parse number “{v}”"


def _find_user(token):
    token = _s(token)
    if not token:
        return None, None
    user = User.objects.filter(username__iexact=token).first() or User.objects.filter(email__iexact=token).first()
    if user is None:
        return None, f"No user found matching “{token}”, left blank"
    return user, None


def _find_by_name(qs, token, label):
    token = _s(token)
    if not token:
        return None, None
    obj = qs.filter(name__iexact=token).first()
    if obj is None:
        return None, f"No {label} found named “{token}”, left blank"
    return obj, None


def _row(row_num, data, errors, warnings, label):
    return {"row": row_num, "data": data, "errors": errors, "warnings": warnings, "ok": not errors, "label": label}


def _load_columns(fileobj, columns):
    wb = load_workbook(fileobj, data_only=True, read_only=True)
    return _load_columns_from_ws(wb.worksheets[0], columns)


def _load_columns_from_ws(ws, columns):
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    if not header:
        raise ValueError("The sheet is empty — no header row found.")

    def norm(h):
        return str(h).strip().rstrip("*").strip().lower() if h is not None else ""

    header_map = {norm(h): i for i, h in enumerate(header) if h is not None}
    col_index, missing_required, missing_optional = {}, [], []
    for key, label, _ in columns:
        idx = header_map.get(norm(label))
        if idx is None:
            (missing_required if label.endswith("*") else missing_optional).append(label)
        else:
            col_index[key] = idx
    if missing_required:
        raise ValueError("Missing required column(s): " + ", ".join(missing_required))

    def data_rows():
        n = 1
        for raw in rows:
            n += 1
            if raw is None or all(c is None or _s(c) == "" for c in raw):
                continue
            yield n, {key: (raw[idx] if idx < len(raw) else None) for key, idx in col_index.items()}

    return missing_optional, data_rows()


def _resolve_customer_code(name, raw_code, used_codes):
    """Customer.save() only fills a blank code — it doesn't dodge collisions the way
    Project.save() does for `key`. Resolve the final code here so the review table
    shows exactly what will be saved, and a repeat run of the app's own auto-derivation
    (e.g. two customers both starting 'Acme ...') doesn't crash on commit."""
    base = raw_code or ("".join(w[0] for w in name.split()[:3]).upper()[:12] or "CUS")
    candidate, n = base, 1
    while candidate in used_codes:
        n += 1
        candidate = f"{base}{n}"[:12]
    used_codes.add(candidate)
    return candidate


def validate_customers(fileobj):
    missing_optional, rows = _load_columns(fileobj, COLUMNS["customers"])
    return _validate_customers_core(rows), missing_optional


def _validate_customers_core(rows):
    existing_names = {n.lower() for n in Customer.objects.values_list("name", flat=True)}
    used_codes = {c.upper() for c in Customer.objects.values_list("code", flat=True)}
    seen = set()
    results = []
    for row_num, cells in rows:
        errors, warnings = [], []
        name = _s(cells.get("name"))
        if not name:
            errors.append("Name is required")
        elif name.lower() in existing_names:
            errors.append(f"A customer named “{name}” already exists")
        elif name.lower() in seen:
            errors.append(f"“{name}” appears more than once in this file")

        raw_code = _s(cells.get("code")).upper()[:12]
        code = _resolve_customer_code(name, raw_code, used_codes) if name else raw_code
        if raw_code and code != raw_code:
            warnings.append(f"Code “{raw_code}” was taken, used “{code}” instead")

        is_active, w = _bool(cells.get("is_active"))
        if w:
            warnings.append(w)
        account_manager, w = _find_user(cells.get("account_manager"))
        if w:
            warnings.append(w)
        primary_contact, w = _find_user(cells.get("primary_contact"))
        if w:
            warnings.append(w)

        data = {
            "name": name, "code": code, "industry": _s(cells.get("industry")),
            "website": _s(cells.get("website")), "account_manager": account_manager,
            "primary_contact": primary_contact, "contact_email": _s(cells.get("contact_email")),
            "contact_phone": _s(cells.get("contact_phone")), "is_active": is_active,
            "description": _s(cells.get("description")), "notes": _s(cells.get("notes")),
        }
        if not errors:
            seen.add(name.lower())
        results.append(_row(row_num, data, errors, warnings, name or "(blank)"))
    return results


def validate_projects(fileobj):
    missing_optional, rows = _load_columns(fileobj, COLUMNS["projects"])
    return _validate_projects_core(rows), missing_optional


def _validate_projects_core(rows):
    existing_names = {n.lower() for n in Project.objects.values_list("name", flat=True)}
    existing_keys = {k.lower() for k in Project.objects.values_list("key", flat=True)}
    seen_names, seen_keys = set(), set()

    status_map = {}
    for value, label in PROJECT_STATUS:
        status_map[value.lower()] = value
        status_map[label.lower()] = value
    priority_map = {label.lower(): value for value, label in PRIORITIES}

    results = []
    for row_num, cells in rows:
        errors, warnings = [], []
        name = _s(cells.get("name"))
        if not name:
            errors.append("Name is required")
        elif name.lower() in existing_names:
            errors.append(f"A project named “{name}” already exists")
        elif name.lower() in seen_names:
            errors.append(f"“{name}” appears more than once in this file")

        key = _s(cells.get("key")).upper()
        if key:
            if key.lower() in existing_keys:
                errors.append(f"Key “{key}” is already in use")
            elif key.lower() in seen_keys:
                errors.append(f"Key “{key}” appears more than once in this file")

        customer, w = _find_by_name(Customer.objects.all(), cells.get("customer"), "customer")
        if w:
            warnings.append(w)

        status_raw = _s(cells.get("status")).lower()
        status = status_map.get(status_raw, "lead")
        if status_raw and status_raw not in status_map:
            warnings.append(f"Unknown status “{cells.get('status')}”, defaulted to Lead")

        priority_raw = _s(cells.get("priority")).lower()
        priority = priority_map.get(priority_raw, 3)
        if priority_raw and priority_raw not in priority_map:
            warnings.append(f"Unknown priority “{cells.get('priority')}”, defaulted to Medium")

        owner, w = _find_user(cells.get("owner"))
        if w:
            warnings.append(w)
        team, w = _find_by_name(Team.objects.all(), cells.get("team"), "team")
        if w:
            warnings.append(w)

        start_date, e = _date(cells.get("start_date"))
        if e:
            errors.append(e)
        due_date, e = _date(cells.get("due_date"))
        if e:
            errors.append(e)
        if start_date and due_date and due_date < start_date:
            errors.append("Due date is before the start date")

        budget, e = _decimal(cells.get("budget"))
        if e:
            errors.append(e)

        data = {
            "name": name, "key": key, "customer": customer, "description": _s(cells.get("description")),
            "status": status, "priority": priority, "owner": owner, "team": team,
            "start_date": start_date, "due_date": due_date, "budget": budget,
        }
        if not errors:
            seen_names.add(name.lower())
            if key:
                seen_keys.add(key.lower())
        results.append(_row(row_num, data, errors, warnings, name or "(blank)"))
    return results


def validate_people(fileobj):
    missing_optional, rows = _load_columns(fileobj, COLUMNS["people"])
    return _validate_people_core(rows), missing_optional


def _validate_people_core(rows):
    existing_usernames = {u.lower() for u in User.objects.values_list("username", flat=True)}
    seen = set()
    results = []
    for row_num, cells in rows:
        errors, warnings = [], []
        first_name = _s(cells.get("first_name"))
        last_name = _s(cells.get("last_name"))
        if not first_name:
            errors.append("First name is required")

        username = _s(cells.get("username"))
        if username:
            if username.lower() in existing_usernames or username.lower() in seen:
                errors.append(f"Username “{username}” is already taken")
        elif first_name:
            base = slugify(f"{first_name}.{last_name}").strip(".-") or "person"
            candidate, n = base, 1
            while candidate.lower() in existing_usernames or candidate.lower() in seen:
                n += 1
                candidate = f"{base}{n}"
            username = candidate

        email = _s(cells.get("email"))
        if email:
            try:
                validate_email(email)
            except DjangoValidationError:
                errors.append(f"“{email}” is not a valid email address")

        role, w = _find_by_name(Role.objects.all(), cells.get("role"), "role")
        if w:
            warnings.append(w)
        team, w = _find_by_name(Team.objects.all(), cells.get("team"), "team")
        if w:
            warnings.append(w)
        manager, w = _find_user(cells.get("manager"))
        if w:
            warnings.append(w)
        customer, w = _find_by_name(Customer.objects.all(), cells.get("customer"), "customer")
        if w:
            warnings.append(w)

        is_active, w = _bool(cells.get("is_active"))
        if w:
            warnings.append(w)

        data = {
            "first_name": first_name, "last_name": last_name, "username": username, "email": email,
            "phone": _s(cells.get("phone")), "role": role, "team": team, "manager": manager,
            "customer": customer, "title": _s(cells.get("title")), "description": _s(cells.get("description")),
            "responsibility": _s(cells.get("responsibility")), "remark": _s(cells.get("remark")),
            "is_active": is_active, "password": _s(cells.get("password")),
        }
        if not errors:
            seen.add(username.lower())
        results.append(_row(row_num, data, errors, warnings, f"{first_name} {last_name}".strip() or "(blank)"))
    return results


VALIDATORS = {"customers": validate_customers, "projects": validate_projects, "people": validate_people}

# worksheet-scoped entry points, for the combined sheet where each kind lives on its own tab
WS_CORE = {"customers": _validate_customers_core, "projects": _validate_projects_core, "people": _validate_people_core}


def find_sheet(wb, kind):
    from .specs import LABELS

    want = LABELS[kind].strip().lower()
    for ws in wb.worksheets:
        if ws.title.strip().lower() == want:
            return ws
    return None


def validate_ws(kind, ws):
    """Same checks as validate(), but against an already-open worksheet (the combined
    file's tab for this kind) instead of opening kind's own single-sheet file."""
    empty_counts = {"total": 0, "create": 0, "error": 0, "warning": 0}
    try:
        missing_optional, rows = _load_columns_from_ws(ws, COLUMNS[kind])
        rows = WS_CORE[kind](rows)
    except ValueError as exc:
        return {"file_error": str(exc), "rows": [], "missing_optional": [], "counts": empty_counts}

    counts = {
        "total": len(rows),
        "create": sum(1 for r in rows if r["ok"]),
        "error": sum(1 for r in rows if not r["ok"]),
        "warning": sum(1 for r in rows if r["ok"] and r["warnings"]),
    }
    return {"file_error": None, "rows": rows, "missing_optional": missing_optional, "counts": counts}


def validate(kind, fileobj):
    fileobj.seek(0)
    try:
        rows, missing_optional = VALIDATORS[kind](fileobj)
    except ValueError as exc:
        return {"file_error": str(exc), "rows": [], "missing_optional": [], "counts": {}}
    except Exception as exc:
        return {"file_error": f"Could not read this file: {exc}", "rows": [], "missing_optional": [], "counts": {}}

    counts = {
        "total": len(rows),
        "create": sum(1 for r in rows if r["ok"]),
        "error": sum(1 for r in rows if not r["ok"]),
        "warning": sum(1 for r in rows if r["ok"] and r["warnings"]),
    }
    return {"file_error": None, "rows": rows, "missing_optional": missing_optional, "counts": counts}
