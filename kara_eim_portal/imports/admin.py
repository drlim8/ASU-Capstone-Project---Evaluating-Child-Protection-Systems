from django.contrib import admin

from .models import AuditEvent, DatasetImport, PublishedRecord, StagedRecord


@admin.register(DatasetImport)
class DatasetImportAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "jurisdiction", "reporting_year", "uploaded_by", "uploaded_at")
    list_filter = ("status", "jurisdiction", "framework_version")
    search_fields = ("title", "original_filename", "file_sha256")
    readonly_fields = ("file_sha256", "summary", "validation_errors", "validation_warnings", "cluster_summary")


@admin.register(StagedRecord)
class StagedRecordAdmin(admin.ModelAdmin):
    list_display = ("dataset_import", "sheet_name", "source_row", "record_key", "is_valid")
    list_filter = ("sheet_name", "is_valid")
    search_fields = ("record_key",)


@admin.register(PublishedRecord)
class PublishedRecordAdmin(admin.ModelAdmin):
    list_display = ("dataset_import", "sheet_name", "source_row", "record_key", "published_at")
    list_filter = ("sheet_name",)
    search_fields = ("record_key",)


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("dataset_import", "action", "actor", "created_at")
    list_filter = ("action",)
