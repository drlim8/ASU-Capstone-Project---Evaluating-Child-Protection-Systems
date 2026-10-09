"""File type detection by content. No Django imports."""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Literal

VALID_TYPES_MESSAGE = "Valid file types are Excel (.xlsx), PDF, or a URL linking to one of these."

Kind = Literal["pdf", "xlsx", "html", "image", "unknown"]

_IMAGE_SIGNATURES = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"II*\x00",
    b"MM\x00*",
    b"BM",
)


def _is_xlsx(path: Path) -> bool:
    try:
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return False
    return "[Content_Types].xml" in names and any(n.startswith("xl/") for n in names)


def detect_kind(path: Path) -> Kind:
    path = Path(path)
    with open(path, "rb") as f:
        head = f.read(4096)
    if head.startswith(b"%PDF-"):
        return "pdf"
    if _is_xlsx(path):
        return "xlsx"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image"
    if head.startswith(_IMAGE_SIGNATURES):
        return "image"
    text = head[:2048]
    if text.startswith(b"\xef\xbb\xbf"):
        text = text[3:]
    text = text.lstrip().lower()
    if b"<!doctype html" in text or b"<html" in text:
        return "html"
    return "unknown"


_EXPECTED = {
    "pdf": {"application/pdf"},
    "xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/x-zip-compressed",
    },
    "html": {"text/html", "application/xhtml+xml"},
}


def content_type_mismatch(kind: str, content_type: str) -> bool:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if not ct or ct == "application/octet-stream":
        return False
    expected = _EXPECTED.get(kind)
    if expected is None:
        if kind == "image":
            return not ct.startswith("image/")
        return False
    return ct not in expected
