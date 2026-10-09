import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from intake.services.detect import VALID_TYPES_MESSAGE, content_type_mismatch, detect_kind
from intake.services.types import is_decorative
from intake.tests import factories


class DetectKindTests(SimpleTestCase):
    def _kind(self, data: bytes) -> str:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "f.bin"
            p.write_bytes(data)
            return detect_kind(p)

    def test_detects_pdf(self):
        self.assertEqual(self._kind(factories.text_pdf()), "pdf")

    def test_detects_xlsx(self):
        self.assertEqual(self._kind(factories.link_workbook()), "xlsx")

    def test_detects_html_with_bom_and_leading_whitespace(self):
        data = b"\xef\xbb\xbf  \n\t<!DOCTYPE HTML><html><body>x</body></html>"
        self.assertEqual(self._kind(data), "html")
        self.assertEqual(self._kind(factories.html_page()), "html")

    def test_png_and_jpeg_are_image(self):
        self.assertEqual(self._kind(factories.png_bytes()), "image")
        self.assertEqual(self._kind(b"\xff\xd8\xff\xe0" + b"\x00" * 20), "image")

    def test_docx_zip_is_unknown(self):
        self.assertEqual(self._kind(factories.docx_bytes()), "unknown")

    def test_random_bytes_unknown(self):
        self.assertEqual(self._kind(bytes(range(256))), "unknown")

    def test_message_verbatim(self):
        self.assertEqual(
            VALID_TYPES_MESSAGE,
            "Valid file types are Excel (.xlsx), PDF, or a URL linking to one of these.",
        )

    def test_content_type_mismatch(self):
        self.assertTrue(content_type_mismatch("pdf", "text/html; charset=utf-8"))
        self.assertFalse(content_type_mismatch("pdf", "application/pdf"))
        self.assertFalse(content_type_mismatch("pdf", ""))
        self.assertFalse(content_type_mismatch("pdf", "application/octet-stream"))


class DecorativeTests(SimpleTestCase):
    def test_is_decorative(self):
        self.assertTrue(is_decorative(63, 500))
        self.assertTrue(is_decorative(99, 100))
        self.assertFalse(is_decorative(100, 100))
