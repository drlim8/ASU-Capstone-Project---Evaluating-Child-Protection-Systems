import hashlib
from pathlib import Path

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone


def import_upload_path(instance, filename):
    safe_name = Path(filename).name
    uploaded_at = instance.uploaded_at or timezone.now()
    return f"imports/{uploaded_at:%Y/%m/%d}/{safe_name}"


class DatasetImport(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        VALIDATING = "validating", "Validating"
        NEEDS_CORRECTION = "needs_correction", "Needs correction"
        READY = "ready", "Ready for review"
        APPROVED = "approved", "Approved and published"
        REJECTED = "rejected", "Rejected"
        FAILED = "failed", "Processing failed"

    title = models.CharField(max_length=200)
    jurisdiction = models.CharField(max_length=100, blank=True)
    reporting_year = models.PositiveIntegerField(null=True, blank=True)
    framework_version = models.CharField(max_length=50, blank=True)
    uploaded_file = models.FileField(
        upload_to=import_upload_path,
        validators=[FileExtensionValidator(["xlsx"])],
    )
    original_filename = models.CharField(max_length=255)
    file_sha256 = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.UPLOADED, db_index=True)
    selected_sheets = models.JSONField(default=list, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    validation_errors = models.JSONField(default=list, blank=True)
    validation_warnings = models.JSONField(default=list, blank=True)
    cluster_summary = models.JSONField(default=dict, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="eim_uploads")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="eim_reviews",
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.title} ({self.get_status_display()})"

    @staticmethod
    def calculate_sha256(file_obj):
        digest = hashlib.sha256()
        position = file_obj.tell()
        file_obj.seek(0)
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
        file_obj.seek(position)
        return digest.hexdigest()

    @property
    def can_approve(self):
        return self.status == self.Status.READY and not self.validation_errors


class StagedRecord(models.Model):
    dataset_import = models.ForeignKey(DatasetImport, on_delete=models.CASCADE, related_name="staged_records")
    sheet_name = models.CharField(max_length=100, db_index=True)
    source_row = models.PositiveIntegerField()
    record_key = models.CharField(max_length=200, blank=True, db_index=True)
    payload = models.JSONField(default=dict)
    is_valid = models.BooleanField(default=True, db_index=True)
    issues = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["sheet_name", "source_row"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset_import", "sheet_name", "source_row"],
                name="unique_staged_source_row",
            )
        ]

    def __str__(self):
        return f"{self.sheet_name} row {self.source_row}"


class PublishedRecord(models.Model):
    dataset_import = models.ForeignKey(DatasetImport, on_delete=models.PROTECT, related_name="published_records")
    sheet_name = models.CharField(max_length=100, db_index=True)
    source_row = models.PositiveIntegerField()
    record_key = models.CharField(max_length=200, blank=True, db_index=True)
    payload = models.JSONField(default=dict)
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sheet_name", "source_row"]
        constraints = [
            models.UniqueConstraint(
                fields=["dataset_import", "sheet_name", "source_row"],
                name="unique_published_source_row",
            )
        ]


class AuditEvent(models.Model):
    dataset_import = models.ForeignKey(DatasetImport, on_delete=models.CASCADE, related_name="audit_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    action = models.CharField(max_length=80)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} at {self.created_at:%Y-%m-%d %H:%M}"
