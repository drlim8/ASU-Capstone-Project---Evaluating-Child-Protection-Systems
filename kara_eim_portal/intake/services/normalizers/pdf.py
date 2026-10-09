"""PDF normalizer: text/tables via pdfplumber, previews/images via pypdfium2. No Django imports."""
from __future__ import annotations

import io
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from pdfminer.pdfparser import PDFSyntaxError
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.psparser import PSException
from pypdfium2 import PdfiumError

from intake.services.types import (
    ImageData,
    IntakeWarning,
    NormalizedResult,
    NormalizeError,
    PageData,
    TableData,
)

UNREADABLE_MESSAGE = "This PDF is password-protected or damaged and could not be read."
MIN_TEXT_CHARS = 20
PREVIEW_SCALE = 150 / 72


def _cell(value):
    if value is None:
        return None
    return value if value.strip() else None


def _png(pil_image) -> bytes:
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return buf.getvalue()


def _extract_images(page, page_no: int, warnings: list[IntakeWarning]) -> list[ImageData]:
    images: list[ImageData] = []
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
        try:
            pil = obj.get_bitmap().to_pil()
            bbox = [float(v) for v in obj.get_pos()]
            images.append(
                ImageData(
                    index=len(images),
                    content=_png(pil),
                    ext="png",
                    width=pil.width,
                    height=pil.height,
                    bbox=bbox,
                )
            )
        except Exception:  # noqa: BLE001 - one bad image must not fail the document
            warnings.append(
                IntakeWarning(
                    "image_unreadable",
                    f"An image on page {page_no} could not be read and was skipped.",
                    page=page_no,
                )
            )
    return images


def normalize_pdf(path: Path) -> NormalizedResult:
    warnings: list[IntakeWarning] = []
    pages: list[PageData] = []
    try:
        pdf = pdfium.PdfDocument(str(path))
    except (PdfiumError, OSError) as exc:
        raise NormalizeError(UNREADABLE_MESSAGE) from exc
    try:
        if len(pdf) == 0:
            raise NormalizeError(UNREADABLE_MESSAGE)
        try:
            plumber = pdfplumber.open(str(path))
        except (PDFSyntaxError, PDFPasswordIncorrect, PSException, OSError) as exc:
            raise NormalizeError(UNREADABLE_MESSAGE) from exc
        try:
            for i, ppage in enumerate(plumber.pages):
                n = i + 1
                try:
                    text = ppage.extract_text() or ""
                    tables = []
                    for t in ppage.find_tables():
                        rows = [[_cell(c) for c in row] for row in t.extract()]
                        tables.append(
                            TableData(
                                index=len(tables),
                                rows=rows,
                                bbox=[float(v) for v in t.bbox],
                                header_guess=list(rows[0]) if rows else None,
                            )
                        )
                except (PDFSyntaxError, PDFPasswordIncorrect, PSException) as exc:
                    raise NormalizeError(UNREADABLE_MESSAGE) from exc
                dpage = pdf[i]
                try:
                    preview = _png(dpage.render(scale=PREVIEW_SCALE).to_pil())
                    images = _extract_images(dpage, n, warnings)
                finally:
                    dpage.close()
                has_text = len("".join(text.split())) >= MIN_TEXT_CHARS
                pages.append(
                    PageData(
                        number=n,
                        text=text,
                        has_text_layer=has_text,
                        preview_png=preview,
                        tables=tables,
                        images=images,
                    )
                )
        finally:
            plumber.close()
    except PdfiumError as exc:
        raise NormalizeError(UNREADABLE_MESSAGE) from exc
    finally:
        pdf.close()

    if pages and not any(p.has_text_layer for p in pages):
        warnings.insert(
            0,
            IntakeWarning(
                code="no_text_layer",
                message="No text layer found — this may be a scanned document and need OCR.",
            ),
        )
    return NormalizedResult(pages=pages, links=[], warnings=warnings)
