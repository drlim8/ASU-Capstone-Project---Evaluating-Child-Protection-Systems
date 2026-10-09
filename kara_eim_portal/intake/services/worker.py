"""Database-backed job queue for intake processing.

Jobs are claimed with a conditional UPDATE (compare-and-set on state and
locked_at), which is safe across several worker processes on both SQLite
and PostgreSQL without SELECT ... FOR UPDATE.
"""
from __future__ import annotations

import logging
import traceback
from datetime import datetime, timedelta

from django.db.models import F, Q
from django.utils import timezone

from intake.models import IntakeEvent, ProcessingJob, SourceDocument

from .fetch import FetchError, HostThrottle
from .pipeline import process_document

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
STALE_LOCK = timedelta(minutes=10)
CLAIM_CANDIDATES = 10

State = ProcessingJob.State


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=30 if attempts == 1 else 120)


def claim_next_job(now: datetime | None = None) -> ProcessingJob | None:
    now = now or timezone.now()
    eligible = Q(state=State.PENDING, run_after__lte=now) | Q(state=State.RUNNING, locked_at__lt=now - STALE_LOCK)
    candidates = list(
        ProcessingJob.objects.filter(eligible)
        .order_by("run_after", "id")
        .values("pk", "state", "locked_at")[:CLAIM_CANDIDATES]
    )
    for seen in candidates:
        query = ProcessingJob.objects.filter(pk=seen["pk"], state=seen["state"])
        if seen["locked_at"] is None:
            query = query.filter(locked_at__isnull=True)
        else:
            query = query.filter(locked_at=seen["locked_at"])
        if query.update(state=State.RUNNING, locked_at=now, attempts=F("attempts") + 1) != 1:
            continue  # another worker won the race
        try:
            return ProcessingJob.objects.get(pk=seen["pk"])
        except ProcessingJob.DoesNotExist:
            continue  # deleted (with its document) right after we claimed it
    return None


def _update_job(job: ProcessingJob, **fields) -> None:
    # .update() rather than .save(): never re-insert a job row that was deleted mid-run.
    ProcessingJob.objects.filter(pk=job.pk).update(**fields)
    for name, value in fields.items():
        setattr(job, name, value)


def _fail_document(job: ProcessingJob, message: str) -> None:
    updated = SourceDocument.objects.filter(pk=job.document_id).update(
        status=SourceDocument.Status.FAILED, error=message, updated_at=timezone.now()
    )
    if updated:
        batch_id = SourceDocument.objects.filter(pk=job.document_id).values_list("batch_id", flat=True).first()
        IntakeEvent.objects.create(
            batch_id=batch_id, document_id=job.document_id, action="failed", details={"error": message}
        )


def run_job(job: ProcessingJob, throttle: HostThrottle) -> None:
    try:
        document = SourceDocument.objects.get(pk=job.document_id)
    except SourceDocument.DoesNotExist:
        return

    try:
        process_document(document, throttle=throttle)
    except Exception as exc:  # noqa: BLE001 - every failure must end in a recorded job state
        if not SourceDocument.objects.filter(pk=job.document_id).exists():
            return  # document (and job) deleted while processing
        if isinstance(exc, FetchError) and exc.retryable:
            if job.attempts < MAX_ATTEMPTS:
                _update_job(
                    job,
                    state=State.PENDING,
                    run_after=timezone.now() + _backoff(job.attempts),
                    locked_at=None,
                    last_error=str(exc),
                )
                SourceDocument.objects.filter(pk=job.document_id).update(
                    status=SourceDocument.Status.QUEUED, updated_at=timezone.now()
                )
                return
            _update_job(job, state=State.GAVE_UP, last_error=str(exc))
            _fail_document(job, f"{exc} (gave up after {job.attempts} attempts)")
            return
        if isinstance(exc, FetchError):
            message = str(exc)
        else:
            logger.exception("Unexpected error processing intake document %s", job.document_id)
            message = f"Processing failed unexpectedly ({type(exc).__name__}: {exc})"
        _update_job(job, state=State.GAVE_UP, last_error=traceback.format_exc())
        _fail_document(job, message)
        return

    _update_job(job, state=State.DONE, last_error="")


def run_once(throttle: HostThrottle) -> bool:
    job = claim_next_job()
    if job is None:
        return False
    run_job(job, throttle)
    return True
