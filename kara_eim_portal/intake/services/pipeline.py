"""Process one SourceDocument: fetch, detect, de-duplicate, normalize, persist (spec section 4, steps 2-6)."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import transaction
from django.utils import timezone

from intake.models import HTML_SNAPSHOT_EXT, IntakeEvent, SourceDocument

from .detect import VALID_TYPES_MESSAGE, content_type_mismatch, detect_kind
from .fetch import FetchError, HostThrottle, fetch_bytes, fetch_to_file
from .normalizers.html import normalize_html
from .normalizers.pdf import normalize_pdf
from .normalizers.xlsx import normalize_xlsx
from .persist import persist_result
from .types import NormalizeError

IMAGE_MAX_BYTES = 10 * 1024 * 1024
SUPPORTED_KINDS = {SourceDocument.Kind.PDF, SourceDocument.Kind.XLSX, SourceDocument.Kind.HTML}
_EXTENSIONS = {"pdf": "pdf", "xlsx": "xlsx", "html": HTML_SNAPSHOT_EXT}

Status = SourceDocument.Status


def _event(document: SourceDocument, action: str, **details) -> None:
    IntakeEvent.objects.create(batch_id=document.batch_id, document=document, action=action, details=details)


def _warning(code: str, message: str, page: int | None = None) -> dict:
    return {"code": code, "message": message, "page": page}


def _set_status(document: SourceDocument, status: str, *extra_fields: str) -> None:
    document.status = status
    document.save(update_fields=["status", *extra_fields, "updated_at"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _detect(path: Path, content_type: str, is_web_source: bool) -> str:
    """detect_kind from the bytes, plus the spec's Content-Type hint (5.3):
    a fetched page whose bytes look unknown but is served as text/html is HTML."""
    kind = detect_kind(path)
    if kind == "unknown" and is_web_source:
        if (content_type or "").split(";", 1)[0].strip().lower() == "text/html":
            return SourceDocument.Kind.HTML
    return kind


def _fetch(document: SourceDocument, throttle: HostThrottle) -> None:
    """Download source_url and attach it to stored_file. Partial downloads are always discarded."""
    _set_status(document, Status.FETCHING)
    throttle.wait(document.source_url)
    tmp_dir = tempfile.mkdtemp(prefix="intake-fetch-")
    try:
        tmp_path = Path(tmp_dir) / "download"
        with open(tmp_path, "wb") as fh:
            result = fetch_to_file(
                document.source_url,
                fh,
                max_bytes=settings.INTAKE_MAX_BYTES,
                timeout=settings.INTAKE_FETCH_TIMEOUT,
                user_agent=settings.INTAKE_USER_AGENT,
            )
        ext = _EXTENSIONS.get(_detect(tmp_path, result.content_type, True), "bin")
        with open(tmp_path, "rb") as fh:
            document.stored_file.save(f"original.{ext}", File(fh), save=False)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    document.final_url = result.final_url[:2000]
    document.http_status = result.http_status
    document.content_type = (result.content_type or "")[:100]
    document.size_bytes = result.size_bytes
    document.fetched_at = timezone.now()
    document.save(
        update_fields=[
            "stored_file", "final_url", "http_status", "content_type", "size_bytes", "fetched_at", "updated_at",
        ]
    )
    _event(document, "fetched", final_url=document.final_url, http_status=document.http_status,
           size_bytes=document.size_bytes)


def _normalize(document: SourceDocument, kind: str, path: Path, throttle: HostThrottle):
    if kind == SourceDocument.Kind.PDF:
        return normalize_pdf(path)
    if kind == SourceDocument.Kind.XLSX:
        return normalize_xlsx(path)

    def fetch_image(url: str) -> tuple[bytes, str]:
        throttle.wait(url)
        return fetch_bytes(
            url,
            max_bytes=IMAGE_MAX_BYTES,
            timeout=settings.INTAKE_FETCH_TIMEOUT,
            user_agent=settings.INTAKE_USER_AGENT,
        )

    return normalize_html(path, document.final_url or document.source_url, fetch_image)


def _fail(document: SourceDocument, message: str, *extra_fields: str) -> None:
    document.error = message
    _set_status(document, Status.FAILED, "error", *extra_fields)
    _event(document, "failed", error=message)


def process_document(document: SourceDocument, *, throttle: HostThrottle) -> None:
    """Run one document through the pipeline.

    Terminal outcomes (READY, REJECTED, FAILED) are saved and logged here.
    A retryable FetchError is re-raised so the worker can reschedule the job;
    any other unexpected exception also propagates to the worker.
    """
    document.warnings = []
    document.error = ""
    document.duplicate_of = None
    document.save(update_fields=["warnings", "error", "duplicate_of", "updated_at"])

    if not document.stored_file:
        try:
            _fetch(document, throttle)
        except FetchError as exc:
            if exc.retryable:
                raise
            if exc.http_status is not None:
                document.http_status = exc.http_status
            _fail(document, str(exc), "http_status")
            return

    path = Path(document.stored_file.path)
    document.sha256 = _sha256(path)
    if document.size_bytes is None:
        document.size_bytes = path.stat().st_size
    is_web_source = bool(document.final_url or document.source_url)
    kind = _detect(path, document.content_type, is_web_source)
    if kind not in SUPPORTED_KINDS or (kind == SourceDocument.Kind.HTML and not is_web_source):
        document.kind = SourceDocument.Kind.UNKNOWN
        document.error = VALID_TYPES_MESSAGE
        _set_status(document, Status.REJECTED, "sha256", "size_bytes", "kind", "error")
        _event(document, "rejected", detected=kind, error=VALID_TYPES_MESSAGE)
        return

    document.kind = kind
    if content_type_mismatch(kind, document.content_type):
        document.warnings.append(
            _warning(
                "content_type_mismatch",
                f"The server said this is {document.content_type!r}, but the content looks like {kind.upper()}.",
            )
        )
    original = (
        SourceDocument.objects.filter(sha256=document.sha256)
        .exclude(pk=document.pk)
        .order_by("created_at", "id")
        .first()
    )
    if original is not None:
        document.duplicate_of = original
        document.warnings.append(
            _warning("duplicate", f"This file is identical to an earlier document ({original}).")
        )
    _set_status(document, Status.NORMALIZING, "sha256", "size_bytes", "kind", "warnings", "duplicate_of")

    warnings_before = list(document.warnings)
    try:
        result = _normalize(document, kind, path, throttle)
        with transaction.atomic():
            persist_result(document, result)
            _set_status(document, Status.READY)
    except NormalizeError as exc:
        document.warnings = warnings_before
        _fail(document, str(exc), "warnings")
        return

    _event(
        document,
        "normalized",
        kind=kind,
        pages=len(result.pages),
        links=len(result.links),
        warnings=len(document.warnings),
    )
