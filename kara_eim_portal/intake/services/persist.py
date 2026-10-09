"""Write a NormalizedResult to the database and media storage."""
from __future__ import annotations

import hashlib
from dataclasses import asdict

from django.core.files.base import ContentFile

from intake.models import CandidateLink, DocumentPage, ExtractedImage, ExtractedTable, SourceDocument

from .types import NormalizedResult

MAX_URL_LENGTH = 2000


def persist_result(document: SourceDocument, result: NormalizedResult) -> None:
    """Replace the document's pages with the normalized content.

    Callers should run this inside ``transaction.atomic()``. Existing pages
    (and, by cascade, their tables and images) are deleted first so that
    re-processing is idempotent. Their media files are left on disk.
    """
    document.pages.all().delete()

    for page_data in result.pages:
        page = DocumentPage.objects.create(
            document=document,
            number=page_data.number,
            label=page_data.label[:255],
            text=page_data.text,
            has_text_layer=page_data.has_text_layer,
        )
        if page_data.preview_png:
            page.preview_image.save(f"{page.number}.png", ContentFile(page_data.preview_png), save=True)

        for table in page_data.tables:
            ExtractedTable.objects.create(
                page=page,
                index=table.index,
                rows=table.rows,
                bbox=table.bbox,
                header_guess=table.header_guess,
            )

        for image_data in page_data.images:
            image = ExtractedImage(
                page=page,
                index=image_data.index,
                sha256=hashlib.sha256(image_data.content).hexdigest(),
                width=image_data.width,
                height=image_data.height,
                bbox=image_data.bbox,
                src=image_data.src[:MAX_URL_LENGTH],
                alt=image_data.alt,
                caption=image_data.caption,
                decorative=image_data.decorative,
            )
            image.file.save(f"{image_data.index}.{image_data.ext}", ContentFile(image_data.content), save=False)
            image.save()

    for link in result.links:
        if not link.url or len(link.url) > MAX_URL_LENGTH:
            continue
        CandidateLink.objects.get_or_create(
            document=document,
            url=link.url,
            defaults={"label": link.label[:255], "context": link.context[:255]},
        )

    document.warnings = list(document.warnings or []) + [asdict(w) for w in result.warnings]
    document.save(update_fields=["warnings", "updated_at"])
