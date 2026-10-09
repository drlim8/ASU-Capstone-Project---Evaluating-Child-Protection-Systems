from pathlib import Path

from django.conf import settings
from django.db import models


def _ext(filename, default=""):
    suffix = Path(filename).suffix.lower().lstrip(".")
    return suffix or default


# Fetched HTML is stored under a non-rendering name so that serving MEDIA
# (e.g. with DEBUG=1) returns text/plain instead of live HTML on the portal origin.
HTML_SNAPSHOT_EXT = "html.txt"


def document_upload_path(instance, filename):
    """intake/<batch_id>/<doc_id>/original.<ext> (the document row must be saved first)."""
    if filename.lower().endswith("." + HTML_SNAPSHOT_EXT):
        ext = HTML_SNAPSHOT_EXT
    else:
        ext = _ext(filename, "bin")
    return f"intake/{instance.batch_id}/{instance.pk}/original.{ext}"


def page_preview_path(instance, filename):
    """intake/<batch_id>/<doc_id>/pages/<n>.png"""
    doc = instance.document
    return f"intake/{doc.batch_id}/{doc.pk}/pages/{instance.number}.png"


def image_upload_path(instance, filename):
    """intake/<batch_id>/<doc_id>/images/<page>-<index>.<ext>"""
    page = instance.page
    doc = page.document
    ext = _ext(filename, "png")
    return f"intake/{doc.batch_id}/{doc.pk}/images/{page.number}-{instance.index}.{ext}"


class IntakeBatch(models.Model):
    title = models.CharField(max_length=200)
    default_scope = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="intake_batches")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "intake batches"

    def __str__(self):
        return self.title

    @property
    def status(self):
        statuses = set(self.documents.values_list("status", flat=True))
        if statuses & SourceDocument.ACTIVE_STATUSES:
            return "processing"
        if statuses & {SourceDocument.Status.REJECTED, SourceDocument.Status.FAILED}:
            return "has_problems"
        return "ready"


class SourceDocument(models.Model):
    class Kind(models.TextChoices):
        PDF = "pdf", "PDF"
        XLSX = "xlsx", "Excel workbook"
        HTML = "html", "Web page"
        UNKNOWN = "unknown", "Unknown"

    class Origin(models.TextChoices):
        UPLOAD = "upload", "Upload"
        URL = "url", "URL"
        FROM_WORKBOOK = "from_workbook", "Link from workbook"
        FROM_PAGE = "from_page", "Link from page"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        FETCHING = "fetching", "Fetching"
        NORMALIZING = "normalizing", "Normalizing"
        READY = "ready", "Ready"
        REJECTED = "rejected", "Rejected"
        FAILED = "failed", "Failed"

    ACTIVE_STATUSES = {Status.QUEUED, Status.FETCHING, Status.NORMALIZING}

    batch = models.ForeignKey(IntakeBatch, on_delete=models.CASCADE, related_name="documents")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.UNKNOWN)
    origin = models.CharField(max_length=20, choices=Origin.choices)
    source_url = models.URLField(max_length=2000, blank=True)
    final_url = models.URLField(max_length=2000, blank=True)
    http_status = models.IntegerField(null=True, blank=True)
    original_filename = models.CharField(max_length=255, blank=True)
    stored_file = models.FileField(upload_to=document_upload_path, blank=True, max_length=500)
    sha256 = models.CharField(max_length=64, db_index=True, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)
    fetched_at = models.DateTimeField(null=True, blank=True)
    scope = models.CharField(max_length=100, blank=True)
    year_hint = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED, db_index=True)
    warnings = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)
    duplicate_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.original_filename or self.source_url or f"Document {self.pk}"


class CandidateLink(models.Model):
    document = models.ForeignKey(SourceDocument, on_delete=models.CASCADE, related_name="candidate_links")
    url = models.URLField(max_length=2000)
    label = models.CharField(max_length=255, blank=True)
    context = models.CharField(max_length=255, blank=True)
    queued_as = models.ForeignKey(
        SourceDocument, null=True, blank=True, on_delete=models.SET_NULL, related_name="queued_from_links"
    )

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["document", "url"], name="intake_candidatelink_unique_document_url"),
        ]

    def __str__(self):
        return self.url


class DocumentPage(models.Model):
    document = models.ForeignKey(SourceDocument, on_delete=models.CASCADE, related_name="pages")
    number = models.PositiveIntegerField()
    label = models.CharField(max_length=255, blank=True)
    text = models.TextField(blank=True)
    has_text_layer = models.BooleanField(default=True)
    preview_image = models.ImageField(upload_to=page_preview_path, blank=True, max_length=500)

    class Meta:
        ordering = ["document_id", "number"]
        constraints = [
            models.UniqueConstraint(fields=["document", "number"], name="intake_documentpage_unique_document_number"),
        ]

    def __str__(self):
        return f"{self.document} p.{self.number}"


class ExtractedTable(models.Model):
    page = models.ForeignKey(DocumentPage, on_delete=models.CASCADE, related_name="tables")
    index = models.PositiveIntegerField()
    rows = models.JSONField(default=list)
    bbox = models.JSONField(null=True, blank=True)
    header_guess = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["page_id", "index"]

    def __str__(self):
        return f"{self.page} table {self.index}"


class ExtractedImage(models.Model):
    page = models.ForeignKey(DocumentPage, on_delete=models.CASCADE, related_name="images")
    index = models.PositiveIntegerField()
    file = models.ImageField(upload_to=image_upload_path, max_length=500)
    sha256 = models.CharField(max_length=64, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    bbox = models.JSONField(null=True, blank=True)
    src = models.CharField(max_length=2000, blank=True)
    alt = models.TextField(blank=True)
    caption = models.TextField(blank=True)
    decorative = models.BooleanField(default=False)

    class Meta:
        ordering = ["page_id", "index"]

    def __str__(self):
        return f"{self.page} image {self.index}"


class ProcessingJob(models.Model):
    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        GAVE_UP = "gave_up", "Gave up"

    document = models.OneToOneField(SourceDocument, on_delete=models.CASCADE, related_name="job")
    state = models.CharField(max_length=20, choices=State.choices, default=State.PENDING, db_index=True)
    attempts = models.PositiveIntegerField(default=0)
    run_after = models.DateTimeField()
    locked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    def __str__(self):
        return f"Job for {self.document} ({self.state})"


class IntakeEvent(models.Model):
    batch = models.ForeignKey(IntakeBatch, on_delete=models.CASCADE, related_name="events")
    document = models.ForeignKey(
        SourceDocument, null=True, blank=True, on_delete=models.CASCADE, related_name="events"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="intake_events"
    )
    action = models.CharField(max_length=80)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.action} ({self.batch_id})"
