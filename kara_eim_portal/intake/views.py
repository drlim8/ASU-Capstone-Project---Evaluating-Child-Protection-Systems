import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import DocumentMetaForm, IntakeBatchForm
from .models import IntakeBatch, SourceDocument
from .services import batches as batch_service

Status = SourceDocument.Status
ACTIVE = list(SourceDocument.ACTIVE_STATUSES)
PROBLEM = [Status.REJECTED, Status.FAILED]

view_perm = permission_required("intake.view_intakebatch", raise_exception=True)
add_perm = permission_required("intake.add_intakebatch", raise_exception=True)


STATUS_LABELS = {"processing": "Processing", "has_problems": "Has problems", "ready": "Ready"}


def _status(active, problems):
    """Same rule as IntakeBatch.status, from precomputed counts."""
    if active:
        return "processing"
    return "has_problems" if problems else "ready"


@login_required
@view_perm
def home(request):
    batches = list(
        IntakeBatch.objects.select_related("created_by").annotate(
            doc_count=Count("documents", distinct=True),
            active_count=Count("documents", filter=Q(documents__status__in=ACTIVE), distinct=True),
            problem_count=Count("documents", filter=Q(documents__status__in=PROBLEM), distinct=True),
        )[:50]
    )
    for batch in batches:
        batch.status_key = _status(batch.active_count, batch.problem_count)
        batch.status_label = STATUS_LABELS[batch.status_key]
    return render(request, "intake/home.html", {"batches": batches})


@login_required
@add_perm
def batch_new(request):
    if request.method == "POST":
        form = IntakeBatchForm(request.POST, request.FILES)
        if form.is_valid():
            data = form.cleaned_data
            batch = batch_service.create_batch(
                title=data["title"],
                default_scope=data["default_scope"],
                notes=data["notes"],
                files=data["files"],
                urls=data["urls"],
                actor=request.user,
            )
            messages.success(request, f"Batch created with {batch.documents.count()} item(s) queued for processing.")
            return redirect("intake:batch_detail", pk=batch.pk)
    else:
        form = IntakeBatchForm()
    return render(request, "intake/batch_new.html", {"form": form})


def _documents_with_counts(batch):
    return batch.documents.annotate(
        page_count=Count("pages", distinct=True),
        table_count=Count("pages__tables", distinct=True),
        image_count=Count("pages__images", distinct=True),
    )


@login_required
@view_perm
def batch_detail(request, pk):
    batch = get_object_or_404(IntakeBatch.objects.select_related("created_by"), pk=pk)
    documents = list(_documents_with_counts(batch))
    for doc in documents:
        doc.warning_count = len(doc.warnings or [])
    active = sum(d.status in SourceDocument.ACTIVE_STATUSES for d in documents)
    problems = sum(d.status in PROBLEM for d in documents)
    status = _status(active, problems)
    return render(
        request,
        "intake/batch_detail.html",
        {"batch": batch, "documents": documents, "batch_status": status, "status_label": STATUS_LABELS[status]},
    )


@login_required
@view_perm
def batch_status(request, pk):
    batch = get_object_or_404(IntakeBatch, pk=pk)
    documents = list(_documents_with_counts(batch))
    active = sum(d.status in SourceDocument.ACTIVE_STATUSES for d in documents)
    problems = sum(d.status in PROBLEM for d in documents)
    return JsonResponse(
        {
            "status": _status(active, problems),
            "documents": [
                {
                    "id": d.pk,
                    "status": d.status,
                    "pages": d.page_count,
                    "tables": d.table_count,
                    "images": d.image_count,
                    "warnings": len(d.warnings or []),
                }
                for d in documents
            ],
        }
    )


@login_required
@add_perm
@require_POST
def document_retry(request, pk):
    document = get_object_or_404(SourceDocument.objects.select_related("batch"), pk=pk)
    try:
        batch_service.retry_document(document, request.user)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    messages.success(request, "The document was queued again.")
    return redirect("intake:batch_detail", pk=document.batch_id)


@login_required
@add_perm
@require_POST
def document_remove(request, pk):
    document = get_object_or_404(SourceDocument.objects.select_related("batch"), pk=pk)
    batch_id = document.batch_id
    try:
        batch_service.remove_document(document, request.user)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    messages.success(request, "The document was removed from the batch.")
    return redirect("intake:batch_detail", pk=batch_id)


def _int_ids(values):
    ids = []
    for value in values:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


@login_required
@view_perm
def document_detail(request, pk):
    document = get_object_or_404(
        SourceDocument.objects.select_related("batch", "parent", "duplicate_of"), pk=pk
    )
    if request.method == "POST":
        if not request.user.has_perm("intake.add_intakebatch"):
            raise PermissionDenied
        form = DocumentMetaForm(request.POST, instance=document)
        if form.is_valid():
            form.save()
            messages.success(request, "Document details saved.")
            return redirect("intake:document_detail", pk=document.pk)
    else:
        form = DocumentMetaForm(instance=document)
    pages = list(document.pages.prefetch_related("tables", "images"))
    for page in pages:
        images = list(page.images.all())
        page.content_images = [i for i in images if not i.decorative]
        page.decorative_images = [i for i in images if i.decorative]
    return render(
        request,
        "intake/document_detail.html",
        {
            "document": document,
            "form": form,
            "pages": pages,
            "links": list(document.candidate_links.select_related("queued_as")),
            "warnings": document.warnings or [],
        },
    )


@login_required
@add_perm
@require_POST
def document_queue_links(request, pk):
    document = get_object_or_404(SourceDocument.objects.select_related("batch"), pk=pk)
    children = batch_service.queue_links(document, _int_ids(request.POST.getlist("link_ids")), request.user)
    if children:
        messages.success(request, f"{len(children)} link(s) queued for processing.")
    else:
        messages.info(request, "No new links were queued.")
    return redirect("intake:document_detail", pk=document.pk)


@login_required
@view_perm
def document_original(request, pk):
    document = get_object_or_404(SourceDocument, pk=pk)
    if not document.stored_file:
        raise Http404("No stored file for this document.")
    try:
        handle = document.stored_file.open("rb")
    except FileNotFoundError:
        raise Http404("The stored file is missing.")
    name = document.original_filename or os.path.basename(document.stored_file.name)
    return FileResponse(handle, as_attachment=True, filename=name)
