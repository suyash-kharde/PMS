from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.permissions import has_cap, require

from .models import ImportBatch
from .services.combined import run_combined
from .services.commit import commit
from .services.specs import CAPABILITY, COLUMNS, KIND_ORDER, LABELS
from .services.template import build_combined_template, build_template
from .services.validate import validate


def _can_combined(user):
    return all(has_cap(user, CAPABILITY[k]) for k in KIND_ORDER)


def _require_kind(request, kind):
    if kind == "combined":
        if not _can_combined(request.user):
            require(request.user, CAPABILITY[KIND_ORDER[0]])  # raises with a sensible message
        return
    if kind not in LABELS:
        raise Http404
    require(request.user, CAPABILITY[kind])


@login_required
def home(request):
    kinds = [
        {"kind": k, "label": LABELS[k], "can": has_cap(request.user, CAPABILITY[k])}
        for k in LABELS
    ]
    recent = ImportBatch.objects.filter(uploaded_by=request.user)[:10]
    return render(request, "bulk/home.html", {
        "kinds": kinds, "recent": recent, "can_combined": _can_combined(request.user),
    })


@login_required
def download_template(request, kind):
    _require_kind(request, kind)
    if kind == "combined":
        buf = build_combined_template()
        filename = "bulk_import_template.xlsx"
    else:
        buf = build_template(kind)
        filename = f"{kind}_import_template.xlsx"
    return FileResponse(
        buf, as_attachment=True, filename=filename,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@login_required
@require_POST
def upload(request, kind):
    _require_kind(request, kind)
    upload_file = request.FILES.get("file")
    if not upload_file:
        messages.error(request, "Choose a file to upload.")
        return redirect("bulk:home")

    if kind == "combined":
        result = run_combined(upload_file, dry_run=True)
    else:
        result = validate(kind, upload_file)
    if result["file_error"]:
        messages.error(request, result["file_error"])
        return redirect("bulk:home")
    if result["counts"]["total"] == 0:
        messages.error(request, "That sheet has no data rows.")
        return redirect("bulk:home")

    upload_file.seek(0)
    batch = ImportBatch.objects.create(kind=kind, file=upload_file, uploaded_by=request.user)
    return redirect("bulk:review", batch.pk)


@login_required
def review(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk, uploaded_by=request.user)
    _require_kind(request, batch.kind)
    if batch.status != "pending":
        return redirect("bulk:result", batch.pk)

    if batch.kind == "combined":
        with batch.file.open("rb"):
            result = run_combined(batch.file, dry_run=True)
        sections = None
        if not result["file_error"]:
            sections = [{"kind": k, "label": LABELS[k], **result["sections"][k]} for k in KIND_ORDER]
        return render(request, "bulk/review_combined.html", {"batch": batch, "result": result, "sections": sections})

    with batch.file.open("rb"):
        result = validate(batch.kind, batch.file)
    columns = [label for _, label, _ in COLUMNS[batch.kind]]
    return render(request, "bulk/review.html", {
        "batch": batch, "label": LABELS[batch.kind], "result": result, "columns": columns,
    })


@login_required
@require_POST
def confirm(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk, uploaded_by=request.user)
    _require_kind(request, batch.kind)
    if batch.status != "pending":
        return redirect("bulk:result", batch.pk)

    if batch.kind == "combined":
        with batch.file.open("rb"):
            result = run_combined(batch.file, dry_run=False)
        if result["file_error"]:
            messages.error(request, "The file could not be re-read: " + result["file_error"])
            return redirect("bulk:review", batch.pk)
        batch.created_count = sum(len(rows) for rows in result["created"].values())
        batch.skipped_count = result["counts"]["error"]
        request.session[f"bulk_result_{batch.pk}"] = result["created"]
        label = "records"
    else:
        with batch.file.open("rb"):
            result = validate(batch.kind, batch.file)
        if result["file_error"]:
            messages.error(request, "The file could not be re-read: " + result["file_error"])
            return redirect("bulk:review", batch.pk)
        created = commit(batch.kind, result["rows"])
        batch.created_count = len(created)
        batch.skipped_count = result["counts"]["error"]
        request.session[f"bulk_result_{batch.pk}"] = created
        label = LABELS[batch.kind].lower()

    batch.status = "committed"
    batch.committed_at = timezone.now()
    batch.save(update_fields=["status", "created_count", "skipped_count", "committed_at"])
    messages.success(request, f"Imported {batch.created_count} {label}.")
    return redirect("bulk:result", batch.pk)


@login_required
def result(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk, uploaded_by=request.user)
    _require_kind(request, batch.kind)
    if batch.status != "committed":
        return redirect("bulk:review", batch.pk)
    created = request.session.pop(f"bulk_result_{batch.pk}", None)

    if batch.kind == "combined":
        created = created or {}
        sections = [{"kind": k, "label": LABELS[k], "created": created.get(k, [])} for k in KIND_ORDER]
        has_created = any(s["created"] for s in sections)
        return render(request, "bulk/result_combined.html", {"batch": batch, "sections": sections, "has_created": has_created})
    return render(request, "bulk/result.html", {"batch": batch, "label": LABELS[batch.kind], "created": created})


@login_required
@require_POST
def cancel(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk, uploaded_by=request.user)
    _require_kind(request, batch.kind)
    if batch.status == "pending":
        batch.file.delete(save=False)
        batch.delete()
        messages.success(request, "Import cancelled.")
    return redirect("bulk:home")
