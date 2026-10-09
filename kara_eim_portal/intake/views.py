from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import IntakeBatchForm
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
