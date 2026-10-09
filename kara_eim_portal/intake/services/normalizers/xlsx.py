"""XLSX normalizer: one page/table per worksheet, images, URL-column links. No Django imports."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException
from PIL import Image

from imports.services.schema import CORE_SHEETS
from intake.services.types import (
    ImageData,
    IntakeWarning,
    LinkData,
    NormalizedResult,
    NormalizeError,
    PageData,
    TableData,
)

DAMAGED_MESSAGE = "This workbook is damaged and could not be read."
EIM_MESSAGE = "This looks like an EIM workbook. You can also load it through the EIM import."
URL_HEADER_WORDS = ("url", "link")
URL_CONTENT_THRESHOLD = 0.5


def _text(value):
    if value is None:
        return None
    s = str(value)
    return s if s != "" else None


def _is_url(value) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))


def _trim(rows: list[list]) -> list[list]:
    """Trim trailing blank rows and columns (the used range)."""
    while rows and all(c is None for c in rows[-1]):
        rows.pop()
    width = max((i + 1 for r in rows for i, c in enumerate(r) if c is not None), default=0)
    return [r[:width] for r in rows]


def _extract_images(ws, page_no: int, warnings: list[IntakeWarning]) -> list[ImageData]:
    images: list[ImageData] = []
    for img in getattr(ws, "_images", []):
        try:
            data = img._data()
            with Image.open(io.BytesIO(data)) as pil:
                fmt = (pil.format or "PNG").lower()
                width, height = pil.size
            images.append(
                ImageData(
                    index=len(images),
                    content=data,
                    ext="jpg" if fmt == "jpeg" else fmt,
                    width=width,
                    height=height,
                )
            )
        except Exception:  # noqa: BLE001 - one bad image must not fail the workbook
            warnings.append(
                IntakeWarning(
                    "image_unreadable",
                    f"An image on sheet {page_no} could not be read and was skipped.",
                    page=page_no,
                )
            )
    return images


def _url_columns(ws, rows: list[list]) -> tuple[int | None, set[int]]:
    """Return (header row index in `rows`, set of 0-based URL column indexes)."""
    header_idx = next((i for i, r in enumerate(rows) if any(c is not None for c in r)), None)
    if header_idx is None:
        return None, set()
    header = rows[header_idx]
    cols: set[int] = set()
    for c in range(len(header)):
        h = header[c]
        if h is not None and any(w in h.lower() for w in URL_HEADER_WORDS):
            cols.add(c)
            continue
        cells = [r[c] for r in rows[header_idx + 1:] if c < len(r) and r[c] is not None]
        if cells and sum(_is_url(v) for v in cells) / len(cells) >= URL_CONTENT_THRESHOLD:
            cols.add(c)
    return header_idx, cols


def _links(ws, rows: list[list], seen: set[str]) -> list[LinkData]:
    header_idx, cols = _url_columns(ws, rows)
    links: list[LinkData] = []
    if header_idx is None:
        return links
    for c in sorted(cols):
        context = rows[header_idx][c] or ""
        for r in range(header_idx + 1, len(rows)):
            value = rows[r][c] if c < len(rows[r]) else None
            if not _is_url(value):
                continue
            url = value.strip()
            if url in seen:
                continue
            seen.add(url)
            coord = ws.cell(row=r + 1, column=c + 1).coordinate
            links.append(LinkData(url=url, label=f"{ws.title}!{coord}", context=context))
    return links


def normalize_xlsx(path: Path) -> NormalizedResult:
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True)
    except (zipfile.BadZipFile, InvalidFileException, KeyError, OSError, ValueError) as exc:
        raise NormalizeError(DAMAGED_MESSAGE) from exc
    except Exception as exc:  # noqa: BLE001 - damaged archives surface many error types
        raise NormalizeError(DAMAGED_MESSAGE) from exc
    warnings: list[IntakeWarning] = []
    pages: list[PageData] = []
    links: list[LinkData] = []
    seen: set[str] = set()
    try:
        for n, ws in enumerate(wb.worksheets, start=1):
            rows = _trim([[_text(c) for c in row] for row in ws.iter_rows(values_only=True)])
            header = next((r for r in rows if any(c is not None for c in r)), None)
            tables = [TableData(index=0, rows=rows, header_guess=list(header) if header else None)]
            text = "\n".join("\t".join(c for c in r if c is not None) for r in rows)
            text = "\n".join(line for line in text.split("\n") if line)
            pages.append(
                PageData(
                    number=n,
                    text=text,
                    has_text_layer=True,
                    label=ws.title,
                    tables=tables,
                    images=_extract_images(ws, n, warnings),
                )
            )
            links.extend(_links(ws, rows, seen))
        if set(CORE_SHEETS) <= set(wb.sheetnames):
            warnings.append(IntakeWarning("eim_workbook", EIM_MESSAGE))
    finally:
        wb.close()
    return NormalizedResult(pages=pages, links=links, warnings=warnings)
