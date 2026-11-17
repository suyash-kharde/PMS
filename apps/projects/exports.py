from django.http import HttpResponse
from django.utils import timezone

from .models import PROJECT_STATUS

STATUS_LABELS = dict(PROJECT_STATUS)
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _text(value):
    """Cell text that Excel can never read as a formula (names are user-typed)."""
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def customers_projects_xlsx(projects):
    """Every project in `projects`, grouped by customer (A to Z, projects with no customer last)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    rows = sorted(
        projects,
        key=lambda p: (p.customer_id is None, (p.customer.name if p.customer else "").lower(), p.name.lower()),
    )
    heads = ["#", "Customer", "Project name", "Status", "Project owner", "Team", "VM"]

    wb = Workbook()
    ws = wb.active
    ws.title = "Customers and projects"
    body, bold = Font(name="Arial"), Font(name="Arial", bold=True)
    ws.append([f"Customers and their projects · {timezone.localdate():%d %b %Y} · {len(rows)} projects"])
    ws["A1"].font = Font(name="Arial", bold=True, size=13)
    ws.append([])
    ws.append(heads)
    for cell in ws[3]:
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4F46E5")
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")

    for i, p in enumerate(rows, 1):
        c = p.customer
        ws.append([
            i, _text(c.name) if c else "— no customer —", _text(p.name),
            STATUS_LABELS.get(p.status, p.status),
            _text(p.owner.display_name) if p.owner else "", _text(p.team.name) if p.team else "",
            _text(", ".join(f"{l.vm.name} ({l.vm.host})" for l in p.vm_links.all())),
        ])
        for cell in ws[ws.max_row]:
            cell.font = body
        ws.cell(ws.max_row, 2).font = bold

    for n, head in enumerate(heads, 1):
        width = max([len(head)] + [len(str(ws.cell(r, n).value or "")) for r in range(4, ws.max_row + 1)])
        ws.column_dimensions[get_column_letter(n)].width = min(max(width + 2, 8), 45)
    ws.freeze_panes = "C4"
    ws.auto_filter.ref = f"A3:{get_column_letter(len(heads))}{max(ws.max_row, 3)}"

    response = HttpResponse(content_type=XLSX_TYPE)
    response["Content-Disposition"] = f'attachment; filename="planwise_projects_{timezone.localdate():%Y%m%d}.xlsx"'
    wb.save(response)
    return response
