from django.contrib import admin

from .models import (
    CandidateLink,
    DocumentPage,
    ExtractedImage,
    ExtractedTable,
    IntakeBatch,
    IntakeEvent,
    ProcessingJob,
    SourceDocument,
)


@admin.register(IntakeBatch)
class IntakeBatchAdmin(admin.ModelAdmin):
    list_display = ("title", "default_scope", "created_by", "created_at")
    search_fields = ("title",)


@admin.register(SourceDocument)
class SourceDocumentAdmin(admin.ModelAdmin):
    list_display = ("__str__", "batch", "kind", "origin", "status", "scope", "created_at")
    list_filter = ("status", "kind", "origin")
    search_fields = ("original_filename", "source_url", "sha256")


@admin.register(CandidateLink)
class CandidateLinkAdmin(admin.ModelAdmin):
    list_display = ("url", "document", "label", "queued_as")


@admin.register(DocumentPage)
class DocumentPageAdmin(admin.ModelAdmin):
    list_display = ("document", "number", "label", "has_text_layer")
    list_filter = ("has_text_layer",)


@admin.register(ExtractedTable)
class ExtractedTableAdmin(admin.ModelAdmin):
    list_display = ("page", "index")


@admin.register(ExtractedImage)
class ExtractedImageAdmin(admin.ModelAdmin):
    list_display = ("page", "index", "width", "height", "decorative")
    list_filter = ("decorative",)


@admin.register(ProcessingJob)
class ProcessingJobAdmin(admin.ModelAdmin):
    list_display = ("document", "state", "attempts", "run_after", "locked_at")
    list_filter = ("state",)


@admin.register(IntakeEvent)
class IntakeEventAdmin(admin.ModelAdmin):
    list_display = ("batch", "document", "action", "actor", "created_at")
    list_filter = ("action",)
