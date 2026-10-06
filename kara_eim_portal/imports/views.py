import csv

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import DatasetUploadForm, ReviewForm
from .models import AuditEvent, DatasetImport, PublishedRecord
from .services.etl import process_dataset_import


@login_required
def dashboard(request):
    imports = DatasetImport.objects.select_related("uploaded_by", "reviewed_by")[:25]
    counts = {
        "total": DatasetImport.objects.count(),
        "ready": DatasetImport.objects.filter(status=DatasetImport.Status.READY).count(),
        "needs_correction": DatasetImport.objects.filter(status=DatasetImport.Status.NEEDS_CORRECTION).count(),
        "published": DatasetImport.objects.filter(status=DatasetImport.Status.APPROVED).count(),
    }
    return render(request, "imports/dashboard.html", {"imports": imports, "counts": counts})


@login_required
def upload_dataset(request):
    if request.method == "POST":
        form = DatasetUploadForm(request.POST, request.FILES)
        if form.is_valid():
            dataset_import = form.save(commit=False)
            uploaded = form.cleaned_data["uploaded_file"]
            dataset_import.original_filename = uploaded.name
            dataset_import.file_sha256 = DatasetImport.calculate_sha256(uploaded)
            dataset_import.uploaded_by = request.user
            duplicate = DatasetImport.objects.filter(file_sha256=dataset_import.file_sha256).first()
            dataset_import.save()
            AuditEvent.objects.create(
                dataset_import=dataset_import,
                actor=request.user,
                action="uploaded",
                details={"filename": uploaded.name, "duplicate_of": duplicate.pk if duplicate else None},
            )
            try:
                process_dataset_import(dataset_import, actor=request.user)
            except Exception:
                messages.error(request, "The workbook could not be processed. Review the error on the import page.")
            else:
                if duplicate:
                    messages.warning(request, f"This file matches import #{duplicate.pk}. The new upload was kept as a separate version.")
                messages.success(request, "The workbook was uploaded and validated.")
            return redirect("imports:detail", pk=dataset_import.pk)
    else:
        form = DatasetUploadForm()
    return render(request, "imports/upload.html", {"form": form})


@login_required
def import_detail(request, pk):
    dataset_import = get_object_or_404(
        DatasetImport.objects.select_related("uploaded_by", "reviewed_by"),
        pk=pk,
    )
    preview_records = dataset_import.staged_records.all()[:20]
    review_form = ReviewForm()
    return render(
        request,
        "imports/detail.html",
        {"dataset_import": dataset_import, "preview_records": preview_records, "review_form": review_form},
    )


@login_required
@require_POST
def revalidate_import(request, pk):
    dataset_import = get_object_or_404(DatasetImport, pk=pk)
    try:
        process_dataset_import(dataset_import, actor=request.user)
    except Exception:
        messages.error(request, "Validation failed. Review the processing error below.")
    else:
        messages.success(request, "Validation completed again using the current rules.")
    return redirect("imports:detail", pk=pk)


@permission_required("imports.change_datasetimport", raise_exception=True)
@require_POST
@transaction.atomic
def approve_import(request, pk):
    dataset_import = get_object_or_404(DatasetImport.objects.select_for_update(), pk=pk)
    form = ReviewForm(request.POST)
    if not form.is_valid() or not dataset_import.can_approve:
        messages.error(request, "This import cannot be approved until all validation errors are resolved.")
        return redirect("imports:detail", pk=pk)

    dataset_import.published_records.all().delete()
    valid_records = dataset_import.staged_records.filter(is_valid=True)
    PublishedRecord.objects.bulk_create(
        [
            PublishedRecord(
                dataset_import=dataset_import,
                sheet_name=record.sheet_name,
                source_row=record.source_row,
                record_key=record.record_key,
                payload=record.payload,
            )
            for record in valid_records.iterator(chunk_size=500)
        ],
        batch_size=500,
    )
    dataset_import.status = DatasetImport.Status.APPROVED
    dataset_import.reviewed_by = request.user
    dataset_import.reviewed_at = timezone.now()
    dataset_import.review_notes = form.cleaned_data["notes"]
    dataset_import.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])
    AuditEvent.objects.create(
        dataset_import=dataset_import,
        actor=request.user,
        action="approved_and_published",
        details={"published_records": dataset_import.published_records.count()},
    )
    messages.success(request, "The validated records were approved and published.")
    return redirect("imports:detail", pk=pk)


@permission_required("imports.change_datasetimport", raise_exception=True)
@require_POST
def reject_import(request, pk):
    dataset_import = get_object_or_404(DatasetImport, pk=pk)
    form = ReviewForm(request.POST)
    if form.is_valid():
        dataset_import.status = DatasetImport.Status.REJECTED
        dataset_import.reviewed_by = request.user
        dataset_import.reviewed_at = timezone.now()
        dataset_import.review_notes = form.cleaned_data["notes"]
        dataset_import.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes"])
        AuditEvent.objects.create(dataset_import=dataset_import, actor=request.user, action="rejected", details={"notes": dataset_import.review_notes})
        messages.success(request, "The import was rejected and remains available in history.")
    return redirect("imports:detail", pk=pk)


@login_required
def download_validation_report(request, pk):
    dataset_import = get_object_or_404(DatasetImport, pk=pk)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="eim-import-{pk}-validation.csv"'
    writer = csv.writer(response)
    writer.writerow(["Severity", "Code", "Sheet", "Row", "Column", "Message"])
    for item in dataset_import.validation_errors + dataset_import.validation_warnings:
        writer.writerow(
            [
                item.get("severity", ""),
                item.get("code", ""),
                item.get("sheet", ""),
                item.get("row", ""),
                item.get("column", ""),
                item.get("message", ""),
            ]
        )
    return response


@login_required
def published_data(request):
    imports = DatasetImport.objects.filter(status=DatasetImport.Status.APPROVED).select_related("reviewed_by")
    return render(request, "imports/published.html", {"imports": imports})
