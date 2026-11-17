from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .specs import COLUMNS, EXAMPLES, KIND_ORDER, LABELS

HEADER_FONT = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill("solid", fgColor="4F46E5")


def _add_data_sheet(wb, kind, title):
    columns = COLUMNS[kind]
    ws = wb.create_sheet(title)
    ws.append([header for _, header, _ in columns])
    for cell in ws[1]:
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"

    for row in EXAMPLES.get(kind, []):
        ws.append(row)
    for cell in ws[2]:
        cell.font = Font(italic=True, color="667085")

    for i, (_, header, _) in enumerate(columns, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(20, len(header) + 4)
    return ws


def _add_notes_sheet(wb, kinds):
    notes = wb.create_sheet("Notes")
    notes.append(["Sheet", "Column", "Notes"] if len(kinds) > 1 else ["Column", "Notes"])
    for cell in notes[1]:
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
    for kind in kinds:
        for _, header, help_text in COLUMNS[kind]:
            notes.append([LABELS[kind], header, help_text] if len(kinds) > 1 else [header, help_text])
    if len(kinds) > 1:
        notes.column_dimensions["A"].width = 16
        notes.column_dimensions["B"].width = 34
        notes.column_dimensions["C"].width = 74
        text_col = 2
    else:
        notes.column_dimensions["A"].width = 34
        notes.column_dimensions["B"].width = 74
        text_col = 1
    for row in notes.iter_rows(min_row=2):
        row[text_col].alignment = Alignment(wrap_text=True, vertical="top")


def build_template(kind):
    wb = Workbook()
    wb.remove(wb.active)
    _add_data_sheet(wb, kind, LABELS[kind][:31])
    _add_notes_sheet(wb, [kind])

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def build_combined_template():
    wb = Workbook()
    wb.remove(wb.active)
    for kind in KIND_ORDER:
        _add_data_sheet(wb, kind, LABELS[kind][:31])
    _add_notes_sheet(wb, KIND_ORDER)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
