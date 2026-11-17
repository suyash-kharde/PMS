"""Excel and PDF exports of the weekly report. Both are built from the exact same data dict the
report page renders (services.weekly_report), so the files always match the screen."""
import io

from django.http import HttpResponse


def _title(data):
    return f"Timesheet report {data['monday']:%d %b %Y} – {data['sunday']:%d %b %Y}"


def _day_heads(data):
    heads = []
    for d in data["days"]:
        tag = " (Holiday)" if d["kind"] == "holiday" else " (Weekend)" if d["kind"] == "weekend" else ""
        if d["kind"] != "holiday" and d.get("holiday_label"):
            tag += f" ({d['holiday_label']})"  # a holiday for some teams only
        heads.append(f"{d['date']:%a %d %b}{tag}")
    return heads


def _filename(data, ext):
    return f"timesheet_{data['monday']:%Y-%m-%d}_to_{data['sunday']:%Y-%m-%d}.{ext}"


def _summary_rows(data):
    heads = (["Employee", "Total hours", "Absent days", "Leave", "Days submitted"]
             + [f"Total {m['label']}" for m in data["months"]])
    rows = []
    for s in data["summary"]:
        absent = "n/a" if s["limited"] else (", ".join(f"{d:%a %d %b}" for d in s["absent"]) or "None")
        leave = "n/a" if s["limited"] else (", ".join(f"{d:%a %d %b} {h}h" for d, h in s["leave"]) or "None")
        rows.append([s["user"].display_name, s["total"], absent, leave, f"{s['submitted']} of 7"]
                    + [m["total"] for m in s["months"]])
    return heads, rows


def _detail_rows(data):
    heads = ["Employee", "Project"] + _day_heads(data) + ["Total"]
    rows = [[r["user"].display_name, f"{r['project'].key} – {r['project'].name}"] + r["cells"] + [r["total"]]
            for r in data["rows"]]
    rows.append(["Total", ""] + data["day_totals"] + [data["grand_total"]])
    return heads, rows


def _notes(data):
    notes = []
    if data["weekends"]:
        notes.append("Weekends: " + ", ".join(f"{d:%a %d %b}" for d in data["weekends"]))
    if data["holidays"]:
        notes.append("Holidays: " + ", ".join(f"{h.date:%a %d %b} {h.label}" for h in data["holidays"]))
    if data["spans_months"]:
        notes.append("Week spans two months: " + "; ".join(f"{m['label']} {m['total']} h" for m in data["months"]))
    return notes


# --------------------------------------------------------------------------- Excel
def report_xlsx(data):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    head_font = Font(name="Arial", bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="4F46E5")
    body = Font(name="Arial")
    bold = Font(name="Arial", bold=True)

    def sheet(ws, heads, rows, widths):
        ws.append([_title(data)])
        ws["A1"].font = Font(name="Arial", bold=True, size=13)
        ws.append([])
        ws.append(heads)
        for cell in ws[3]:
            cell.font, cell.fill = head_font, head_fill
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        for row in rows:
            ws.append(row)
            for cell in ws[ws.max_row]:
                cell.font = body
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[ws.cell(row=3, column=i).column_letter].width = w
        ws.freeze_panes = "A4"

    ws = wb.active
    ws.title = "Hours"
    heads, rows = _detail_rows(data)
    sheet(ws, heads, rows, [24, 34] + [14] * 7 + [10])
    for cell in ws[ws.max_row]:
        cell.font = bold
    for note in _notes(data):
        ws.append([])
        ws.append([note])

    ws2 = wb.create_sheet("Summary")
    heads, rows = _summary_rows(data)
    sheet(ws2, heads, rows, [24, 12, 40, 30, 14] + [14] * len(data["months"]))

    buf = io.BytesIO()
    wb.save(buf)
    response = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{_filename(data, "xlsx")}"'
    return response


# --------------------------------------------------------------------------- PDF
class _Pdf:
    """A tiny PDF writer (A4 landscape, Helvetica) for text tables — no extra library needed."""

    W, H, M = 842, 595, 36

    def __init__(self):
        self.pages, self.ops, self.y = [], [], self.H - self.M

    @staticmethod
    def _esc(text):
        text = str(text).encode("cp1252", "replace").decode("cp1252")  # WinAnsi = cp1252 (has – and …)
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    def text(self, x, y, s, size=8, bold=False):
        font = "F2" if bold else "F1"
        self.ops.append(f"BT /{font} {size} Tf {x:.1f} {y:.1f} Td ({self._esc(s)}) Tj ET")

    def line(self, x1, y1, x2, y2):
        self.ops.append(f"0.8 0.82 0.88 RG 0.5 w {x1:.1f} {y1:.1f} m {x2:.1f} {y2:.1f} l S")

    def new_page(self):
        if self.ops:
            self.pages.append("\n".join(self.ops))
        self.ops, self.y = [], self.H - self.M

    def need(self, height):
        if self.y - height < self.M:
            self.new_page()

    def table(self, heads, rows, widths, size=7.5):
        def fit(s, w):
            s = str(s)
            limit = max(3, int(w / (size * 0.5)))
            return s if len(s) <= limit else s[: limit - 1] + "…"

        def draw(cells, is_bold):
            x = self.M
            for cell, w in zip(cells, widths):
                self.text(x + 2, self.y - size - 2, fit(cell, w), size, is_bold)
                x += w
            self.y -= size + 6
            self.line(self.M, self.y, self.M + sum(widths), self.y)

        self.need((size + 6) * 2)
        draw(heads, True)
        for row in rows:
            if self.y - (size + 6) < self.M:
                self.new_page()
                draw(heads, True)
            draw(row, row and row[0] == "Total")

    def build(self):
        self.new_page()
        objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
                "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
                "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
        kids = []
        for content in self.pages:
            stream = content.encode("cp1252", "replace")
            objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode('latin-1')}\nendstream")
            objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.W} {self.H}] "
                        f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {len(objs)} 0 R >>")
            kids.append(f"{len(objs)} 0 R")
        objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
        out, offsets = io.BytesIO(), []
        out.write(b"%PDF-1.4\n")
        for i, obj in enumerate(objs, 1):
            offsets.append(out.tell())
            out.write(f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1", "replace"))
        xref = out.tell()
        out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
        for off in offsets:
            out.write(f"{off:010d} 00000 n \n".encode())
        out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
        return out.getvalue()


def report_pdf(data):
    pdf = _Pdf()
    pdf.text(pdf.M, pdf.y - 14, _title(data), 14, True)
    pdf.y -= 28
    for note in _notes(data):
        pdf.text(pdf.M, pdf.y - 9, note, 8.5)
        pdf.y -= 13
    pdf.y -= 6
    pdf.text(pdf.M, pdf.y - 10, "Summary", 10, True)
    pdf.y -= 16
    heads, rows = _summary_rows(data)
    pdf.table(heads, rows, [140, 55, 200, 140, 65] + [70] * len(data["months"]))
    pdf.y -= 14
    pdf.need(40)
    pdf.text(pdf.M, pdf.y - 10, "Hours per employee, project and day", 10, True)
    pdf.y -= 16
    heads, rows = _detail_rows(data)
    pdf.table(heads, rows, [110, 150] + [62] * 7 + [36])
    response = HttpResponse(pdf.build(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{_filename(data, "pdf")}"'
    return response
