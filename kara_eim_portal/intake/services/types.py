"""Shared result types for intake normalizers. No Django imports."""
from __future__ import annotations

from dataclasses import dataclass, field


def is_decorative(width: int, height: int) -> bool:
    return width < 64 or height < 64 or width * height < 10_000


@dataclass
class IntakeWarning:
    code: str
    message: str
    page: int | None = None


@dataclass
class TableData:
    index: int
    rows: list[list[str | None]]
    # PDF points, top-left origin: [x0, top, x1, bottom].
    bbox: list[float] | None = None
    header_guess: list[str | None] | None = None


@dataclass
class ImageData:
    index: int
    content: bytes
    ext: str
    width: int
    height: int
    # PDF points, top-left origin: [x0, top, x1, bottom].
    bbox: list[float] | None = None
    src: str = ""
    alt: str = ""
    caption: str = ""

    @property
    def decorative(self) -> bool:
        return is_decorative(self.width, self.height)


@dataclass
class PageData:
    number: int
    text: str
    has_text_layer: bool = True
    label: str = ""
    preview_png: bytes | None = None
    tables: list[TableData] = field(default_factory=list)
    images: list[ImageData] = field(default_factory=list)


@dataclass
class LinkData:
    url: str
    label: str
    context: str = ""


@dataclass
class NormalizedResult:
    pages: list[PageData]
    links: list[LinkData]
    warnings: list[IntakeWarning]


class NormalizeError(Exception):
    """Non-retryable failure whose message is shown to users."""
