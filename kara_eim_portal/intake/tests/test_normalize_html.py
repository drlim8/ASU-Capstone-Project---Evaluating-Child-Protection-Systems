import tempfile
import unittest
from pathlib import Path
from unittest import mock

from intake.services.fetch import FetchError
from intake.services.normalizers.html import normalize_html
from intake.tests import factories

BASE = "https://dhs.state.mn.us/reports/page.html"


class NormalizeHtmlTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _run(self, data: bytes, fetch_image=None):
        p = Path(self._tmp.name) / "page.html"
        p.write_bytes(data)
        if fetch_image is None:
            fetch_image = mock.Mock(return_value=(factories.png_bytes(200, 150), "image/png"))
        return normalize_html(p, BASE, fetch_image), fetch_image

    def test_text_excludes_nav_and_script(self):
        result, _ = self._run(
            factories.html_page(script="var secretTracker = 1;", nav_label="Main menu")
        )
        self.assertEqual(len(result.pages), 1)
        self.assertEqual(result.pages[0].number, 1)
        text = result.pages[0].text
        self.assertIn("Foster care placements rose", text)
        self.assertNotIn("Home", text)
        self.assertNotIn("secretTracker", text)

    def test_table_with_colspan_and_blank_cells(self):
        result, _ = self._run(factories.html_page())
        table = result.pages[0].tables[0]
        self.assertEqual(
            table.rows, [["Year", "Count"], ["Totals", "Totals"], ["2023", None]]
        )
        self.assertEqual(table.header_guess, ["Year", "Count"])

    def test_rowspan_expands(self):
        html = b"<table><tr><td rowspan='2'>A</td><td>B</td></tr><tr><td>C</td></tr></table>"
        result, _ = self._run(html)
        self.assertEqual(result.pages[0].tables[0].rows, [["A", "B"], ["A", "C"]])

    def test_images_resolve_relative_and_decode_data_uri(self):
        result, fetch = self._run(factories.html_page())
        urls = [c.args[0] for c in fetch.call_args_list]
        self.assertEqual(
            urls,
            [
                "https://example.org/img/abs.png",
                "https://dhs.state.mn.us/img/chart.png",
                "https://cdn.example.org/img/proto.png",
            ],
        )
        images = result.pages[0].images
        self.assertEqual(len(images), 4)
        chart = images[1]
        self.assertEqual(chart.alt, "Chart")
        self.assertEqual(chart.caption, "Figure 1: Placements")
        self.assertEqual((chart.width, chart.height, chart.ext), (200, 150, "png"))
        inline = images[3]
        self.assertEqual((inline.width, inline.height), (8, 8))
        self.assertTrue(inline.decorative)
        self.assertEqual([i.index for i in images], [0, 1, 2, 3])

    def test_malformed_and_unsupported_images(self):
        html = (
            b'<img src="data:image/png;base64,@@@@" alt="bad">'
            b'<img src="javascript:alert(1)"><img alt="nosrc">'
            b'<img src="data:image/png,%89PNG">'
        )
        result, fetch = self._run(html)
        fetch.assert_not_called()
        self.assertEqual(result.pages[0].images, [])
        codes = [w.code for w in result.warnings]
        self.assertEqual(codes.count("image_unreadable"), 2)

    def test_image_fetch_failure_is_warning_not_error(self):
        fetch = mock.Mock(side_effect=FetchError("boom"))
        result, _ = self._run(factories.html_page(), fetch)
        failed = [w for w in result.warnings if w.code == "image_fetch_failed"]
        self.assertEqual(len(failed), 3)
        self.assertIn("../img/chart.png", " ".join(w.message for w in failed))
        self.assertEqual(len(result.pages[0].images), 1)  # the data: image

    def test_image_limit(self):
        html = "".join(f'<img src="/i/{n}.png">' for n in range(101)).encode()
        result, fetch = self._run(html)
        self.assertEqual(fetch.call_count, 100)
        self.assertEqual(len(result.pages[0].images), 100)
        self.assertEqual([w.code for w in result.warnings].count("image_limit"), 1)

    def test_unreadable_image_bytes(self):
        fetch = mock.Mock(return_value=(b"not an image", "image/png"))
        result, _ = self._run(b'<img src="/x.png">', fetch)
        self.assertEqual(result.pages[0].images, [])
        self.assertEqual(result.warnings[0].code, "image_unreadable")
        self.assertEqual(result.warnings[0].page, 1)

    def test_candidate_links_pdf_xlsx_xls_and_download_text(self):
        result, _ = self._run(factories.html_page())
        self.assertEqual(
            [l.url for l in result.links],
            [
                "https://dhs.state.mn.us/files/report.pdf",
                "https://dhs.state.mn.us/files/data.xlsx",
                "https://dhs.state.mn.us/files/old.xls",
                "https://dhs.state.mn.us/files/get?id=3",
            ],
        )
        self.assertEqual(result.links[0].label, "Annual report")

    def test_links_dedupe_skip_nonhttp_and_label_fallback(self):
        html = (
            b'<a href="/a.PDF?x=1">A</a><a href="/a.PDF?x=1">again</a>'
            b'<a href="mailto:x@y.org">Download</a><a href="#top">Download</a>'
            b'<a href="/b.pdf"> </a>'
        )
        result, _ = self._run(html)
        self.assertEqual(
            [(l.url, l.label) for l in result.links],
            [
                ("https://dhs.state.mn.us/a.PDF?x=1", "A"),
                ("https://dhs.state.mn.us/b.pdf", "https://dhs.state.mn.us/b.pdf"),
            ],
        )

    def test_js_only_page_warns(self):
        result, _ = self._run(factories.js_only_html())
        w = [x for x in result.warnings if x.code == "js_rendered"]
        self.assertEqual(len(w), 1)
        self.assertIn("consider uploading the PDF instead", w[0].message)
