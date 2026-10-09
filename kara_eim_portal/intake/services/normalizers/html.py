"""HTML normalizer: one page with text, tables, images and candidate document links. No Django imports."""
from __future__ import annotations

import base64
import binascii
import io
import re
from pathlib import Path
from typing import Callable
from urllib.parse import unquote_to_bytes, urljoin, urlsplit

from bs4 import BeautifulSoup
from PIL import Image

from intake.services.fetch import FetchError
from intake.services.types import (
    ImageData,
    IntakeWarning,
    LinkData,
    NormalizedResult,
    PageData,
    TableData,
)

MAX_IMAGES = 100
JS_TEXT_THRESHOLD = 200
LINK_EXTENSIONS = (".pdf", ".xlsx", ".xls")
REMOVE_TAGS = ("script", "style", "nav", "header", "footer", "noscript")
JS_MESSAGE = "This page may need a browser to render; consider uploading the PDF instead."


def _cell_text(cell) -> str | None:
    text = " ".join(cell.get_text(" ").split())
    return text or None


def _span(cell, attr: str) -> int:
    try:
        return max(1, int(str(cell.get(attr, 1)).strip()))
    except ValueError:
        return 1


def _table_rows(table) -> list[list[str | None]]:
    """Expand colspan/rowspan into a rectangular grid."""
    grid: dict[tuple[int, int], str | None] = {}
    trs = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    for r, tr in enumerate(trs):
        c = 0
        for cell in tr.find_all(["td", "th"], recursive=False):
            while (r, c) in grid:
                c += 1
            value = _cell_text(cell)
            for dr in range(_span(cell, "rowspan")):
                for dc in range(_span(cell, "colspan")):
                    grid[(r + dr, c + dc)] = value
            c += _span(cell, "colspan")
    if not grid:
        return []
    height = max(r for r, _ in grid) + 1
    width = max(c for _, c in grid) + 1
    return [[grid.get((r, c)) for c in range(width)] for r in range(height)]


def _decode_data_uri(uri: str) -> bytes:
    header, sep, payload = uri.partition(",")
    if not sep:
        raise ValueError("malformed data URI")
    if header.lower().endswith(";base64"):
        try:
            return base64.b64decode(unquote_to_bytes(payload), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("bad base64") from exc
    return unquote_to_bytes(payload)


def _image(content: bytes, index: int, src: str, alt: str, caption: str) -> ImageData:
    with Image.open(io.BytesIO(content)) as pil:
        fmt = (pil.format or "").lower()
        width, height = pil.size
    if not fmt:
        raise ValueError("unknown image format")
    return ImageData(
        index=index,
        content=content,
        ext="jpg" if fmt == "jpeg" else fmt,
        width=width,
        height=height,
        src=src,
        alt=alt,
        caption=caption,
    )


def _extract_images(soup, base_url, fetch_image, warnings) -> list[ImageData]:
    images: list[ImageData] = []
    seen = 0
    for tag in soup.find_all("img"):
        src = (tag.get("src") or "").strip()
        if not src:
            continue
        resolved = src if src.lower().startswith("data:") else urljoin(base_url, src)
        is_data = resolved.lower().startswith("data:")
        if not is_data and urlsplit(resolved).scheme not in ("http", "https"):
            continue
        if seen >= MAX_IMAGES:
            warnings.append(
                IntakeWarning(
                    "image_limit",
                    f"This page has more than {MAX_IMAGES} images; the rest were skipped.",
                    page=1,
                )
            )
            break
        seen += 1
        shown = "inline data image" if is_data else src
        try:
            content = _decode_data_uri(resolved) if is_data else fetch_image(resolved)[0]
        except FetchError as exc:
            warnings.append(
                IntakeWarning("image_fetch_failed", f"Could not fetch image {src}: {exc}", page=1)
            )
            continue
        except ValueError:
            warnings.append(
                IntakeWarning("image_unreadable", f"Image {shown} could not be read and was skipped.", page=1)
            )
            continue
        figure = tag.find_parent("figure")
        figcaption = figure.find("figcaption") if figure else None
        caption = " ".join(figcaption.get_text(" ").split()) if figcaption else ""
        try:
            images.append(_image(content, len(images), src, tag.get("alt") or "", caption))
        except Exception:  # noqa: BLE001 - Pillow raises many types on bad bytes
            warnings.append(
                IntakeWarning("image_unreadable", f"Image {shown} could not be read and was skipped.", page=1)
            )
    return images


def _extract_links(soup, base_url) -> list[LinkData]:
    links: list[LinkData] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#"):
            continue
        url = urljoin(base_url, href)
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            continue
        label = " ".join(a.get_text(" ").split())
        if not (parts.path.lower().endswith(LINK_EXTENSIONS) or "download" in label.lower()):
            continue
        if url in seen:
            continue
        seen.add(url)
        links.append(LinkData(url=url, label=label or url))
    return links


def normalize_html(
    path: Path, base_url: str, fetch_image: Callable[[str], tuple[bytes, str]]
) -> NormalizedResult:
    soup = BeautifulSoup(Path(path).read_bytes(), "html.parser")
    warnings: list[IntakeWarning] = []
    has_script = soup.find("script") is not None

    tables = []
    for table in soup.find_all("table"):
        rows = _table_rows(table)
        if rows:
            tables.append(TableData(index=len(tables), rows=rows, header_guess=list(rows[0])))
    images = _extract_images(soup, base_url, fetch_image, warnings)
    links = _extract_links(soup, base_url)

    for tag in soup.find_all(REMOVE_TAGS):
        tag.decompose()
    text = re.sub(r"\s*\n\s*", "\n", re.sub(r"[ \t\r\f\v]+", " ", soup.get_text("\n"))).strip()

    if len(text) < JS_TEXT_THRESHOLD and has_script:
        warnings.append(IntakeWarning("js_rendered", JS_MESSAGE, page=1))

    page = PageData(
        number=1,
        text=text,
        has_text_layer=len("".join(text.split())) >= 20,
        tables=tables,
        images=images,
    )
    return NormalizedResult(pages=[page], links=links, warnings=warnings)
