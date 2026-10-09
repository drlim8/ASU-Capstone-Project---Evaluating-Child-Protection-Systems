import tempfile
import unittest
from pathlib import Path

from intake.services.normalizers.pdf import normalize_pdf
from intake.services.types import NormalizeError
from intake.tests import factories


class NormalizePdfTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _write(self, data: bytes) -> Path:
        p = Path(self._tmp.name) / "doc.pdf"
        p.write_bytes(data)
        return p

    def test_text_pdf_has_text_table_and_preview(self):
        result = normalize_pdf(self._write(factories.text_pdf()))
        self.assertEqual(len(result.pages), 1)
        page = result.pages[0]
        self.assertIn("7763", page.text)
        self.assertTrue(page.has_text_layer)
        self.assertGreaterEqual(len(page.tables), 1)
        cells = [c for row in page.tables[0].rows for c in row]
        self.assertIn(None, cells)
        self.assertNotIn("", cells)
        self.assertEqual(page.tables[0].header_guess, page.tables[0].rows[0])
        self.assertTrue(page.preview_png.startswith(b"\x89PNG"))
        self.assertEqual(result.warnings, [])

    def test_pdf_with_image_extracts_image_with_bbox(self):
        result = normalize_pdf(self._write(factories.pdf_with_image()))
        page = result.pages[0]
        self.assertEqual(len(page.images), 1)
        img = page.images[0]
        self.assertAlmostEqual(img.width, 200, delta=2)
        self.assertEqual(len(img.bbox), 4)
        self.assertTrue(all(isinstance(v, float) for v in img.bbox))
        self.assertFalse(img.decorative)
        self.assertEqual(img.ext, "png")
        self.assertTrue(img.content.startswith(b"\x89PNG"))

    def test_image_only_pdf_warns_no_text_layer(self):
        result = normalize_pdf(self._write(factories.image_only_pdf()))
        self.assertFalse(result.pages[0].has_text_layer)
        self.assertIn("no_text_layer", [w.code for w in result.warnings])
        self.assertTrue(result.pages[0].preview_png.startswith(b"\x89PNG"))

    def test_corrupt_pdf_raises_normalize_error(self):
        with self.assertRaises(NormalizeError):
            normalize_pdf(self._write(b"%PDF-1.4 garbage"))
