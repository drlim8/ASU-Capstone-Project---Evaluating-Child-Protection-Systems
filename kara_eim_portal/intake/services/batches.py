"""Batch creation, retry and removal (called from views)."""
from __future__ import annotations

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from intake.models import CandidateLink, IntakeBatch, IntakeEvent, ProcessingJob, SourceDocument

Status = SourceDocument.Status


def parse_urls(text: str) -> list[str]:
    """Split pasted text into unique http(s) URLs, keeping order."""
    urls: list[str] = []
    bad: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            parts = urlsplit(line)
            valid = parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
        except ValueError:
            valid = False
        if not valid:
            bad.append(line)
        elif line not in urls:
            urls.append(line)
    if bad:
        raise ValidationError(
            "These lines are not valid http(s) URLs: %(lines)s", params={"lines": "; ".join(bad)}
        )
    return urls


def _queue(document: SourceDocument) -> None:
    ProcessingJob.objects.create(document=document, state=ProcessingJob.State.PENDING, run_after=timezone.now())


@transaction.atomic
def create_batch(*, title, default_scope, notes, files, urls, actor) -> IntakeBatch:
    batch = IntakeBatch.objects.create(
        title=title, default_scope=default_scope, notes=notes, created_by=actor
    )
    IntakeEvent.objects.create(
        batch=batch, actor=actor, action="batch_created", details={"files": len(files), "urls": len(urls)}
    )
    for upload in files:
        document = SourceDocument.objects.create(
            batch=batch,
            origin=SourceDocument.Origin.UPLOAD,
            original_filename=upload.name[:255],
            scope=default_scope,
            status=Status.QUEUED,
        )
        # The row must exist first so the upload path can use its pk.
        document.stored_file.save(upload.name, upload, save=True)
        _queue(document)
        IntakeEvent.objects.create(
            batch=batch, document=document, actor=actor, action="document_added",
            details={"origin": "upload", "filename": upload.name},
        )
    for url in urls:
        document = SourceDocument.objects.create(
            batch=batch,
            origin=SourceDocument.Origin.URL,
            source_url=url,
            scope=default_scope,
            status=Status.QUEUED,
        )
        _queue(document)
        IntakeEvent.objects.create(
            batch=batch, document=document, actor=actor, action="document_added",
            details={"origin": "url", "url": url},
        )
    return batch


@transaction.atomic
def retry_document(document: SourceDocument, actor) -> None:
    if document.status != Status.FAILED:
        raise ValueError("Only failed documents can be retried.")
    ProcessingJob.objects.update_or_create(
        document=document,
        defaults={
            "state": ProcessingJob.State.PENDING,
            "attempts": 0,
            "run_after": timezone.now(),
            "locked_at": None,
            "last_error": "",
        },
    )
    document.status = Status.QUEUED
    document.error = ""
    document.save(update_fields=["status", "error", "updated_at"])
    IntakeEvent.objects.create(batch=document.batch, document=document, actor=actor, action="retried")


@transaction.atomic
def remove_document(document: SourceDocument, actor) -> None:
    if document.status == Status.READY:
        raise ValueError("Ready documents cannot be removed.")
    details = {
        "document_id": document.pk,
        "filename": document.original_filename,
        "url": document.source_url,
    }
    batch = document.batch
    document.delete()
    # document=None: the event must outlive the deleted document.
    IntakeEvent.objects.create(batch=batch, actor=actor, action="removed", details=details)


@transaction.atomic
def queue_links(document: SourceDocument, link_ids: list[int], actor) -> list[SourceDocument]:
    """Turn selected, not-yet-queued candidate links of ``document`` into child documents."""
    links = list(
        CandidateLink.objects.select_for_update()
        .filter(pk__in=link_ids, document=document, queued_as__isnull=True)
        .order_by("id")
    )
    origin = (
        SourceDocument.Origin.FROM_WORKBOOK
        if document.kind == SourceDocument.Kind.XLSX
        else SourceDocument.Origin.FROM_PAGE
    )
    children = []
    for link in links:
        child = SourceDocument.objects.create(
            batch=document.batch,
            parent=document,
            origin=origin,
            source_url=link.url,
            scope=document.scope,
            status=Status.QUEUED,
        )
        _queue(child)
        link.queued_as = child
        link.save(update_fields=["queued_as"])
        children.append(child)
    if children:
        IntakeEvent.objects.create(
            batch=document.batch,
            document=document,
            actor=actor,
            action="links_queued",
            details={"children": [c.pk for c in children]},
        )
    return children
