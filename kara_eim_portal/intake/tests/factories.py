"""Deterministic byte-factories for intake tests."""
from __future__ import annotations

import base64
import io
import zipfile

import openpyxl
from PIL import Image, ImageDraw
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def png_bytes(w: int = 200, h: int = 150) -> bytes:
    img = Image.new("RGB", (w, h), (200, 220, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([5, 5, max(6, w - 6), max(6, h - 6)], outline=(10, 10, 120))
    d.line([0, 0, w, h], fill=(180, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def text_pdf(text: str = "Foster care total 7763", with_table: bool = True) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1)
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, text)
    if with_table:
        x0, y0, cw, rh = 72, 600, 100, 24
        rows = [["Year", "Count"], ["2022", "10"], ["2023", ""]]  # last cell blank
        for r, row in enumerate(rows):
            for col, val in enumerate(row):
                x, y = x0 + col * cw, y0 - r * rh
                c.rect(x, y - rh, cw, rh)
                if val:
                    c.drawString(x + 6, y - 16, val)
    c.showPage()
    c.save()
    return buf.getvalue()


def image_only_pdf() -> bytes:
    img = Image.new("RGB", (300, 200), (240, 240, 240))
    ImageDraw.Draw(img).rectangle([20, 20, 280, 180], outline=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def pdf_with_image() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, invariant=1)
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, "Report with a chart image")
    c.drawImage(ImageReader(io.BytesIO(png_bytes(200, 150))), 72, 500, width=200, height=150)
    c.showPage()
    c.save()
    return buf.getvalue()


def link_workbook() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Source Assets"
    ws.append(["Report Asset ID", "Report Asset URL", "Notes"])
    ws.append(["A1", "https://example.org/reports/report1.pdf", "first"])
    ws.append(["A2", "https://example.org/data/report2.xlsx", None])
    ws.append(["A3", None, "no url here"])
    ws["C4"] = "=SUM(1,2)"  # formula, no cached value
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


_TINY_PNG_B64 = base64.b64encode(png_bytes(8, 8)).decode()


def html_page(
    title: str = "Annual Report",
    article: str = "Foster care placements rose during the reporting year.",
    script: str = "var tracking = 1;",
    nav_label: str = "Main menu",
) -> bytes:
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body>
<nav aria-label="{nav_label}"><a href="/home">Home</a></nav>
<script>{script}</script>
<article><p>{article}</p>
<table>
<tr><th>Year</th><th>Count</th></tr>
<tr><td colspan="2">Totals</td></tr>
<tr><td>2023</td><td></td></tr>
</table>
<img src="https://example.org/img/abs.png" alt="Absolute image">
<figure><img src="../img/chart.png" alt="Chart"><figcaption>Figure 1: Placements</figcaption></figure>
<img src="//cdn.example.org/img/proto.png" alt="Protocol relative">
<img src="data:image/png;base64,{_TINY_PNG_B64}" alt="Inline">
<p><a href="/files/report.pdf">Annual report</a>
<a href="/files/data.xlsx">Data</a>
<a href="/files/old.xls">Legacy data</a>
<a href="/files/get?id=3">Download</a></p>
</article></body></html>"""
    return html.encode("utf-8")


def js_only_html() -> bytes:
    return (
        b"<!DOCTYPE html><html><head><title>App</title></head><body>"
        b'<div id="root"></div><script>document.getElementById("root").innerText="hi";</script>'
        b"</body></html>"
    )


def docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()
