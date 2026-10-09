import os
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from intake.models import (
    CandidateLink,
    DocumentPage,
    IntakeBatch,
    IntakeEvent,
    SourceDocument,
)
from intake.services.detect import VALID_TYPES_MESSAGE
from intake.services.fetch import FetchError, FetchResult, HostThrottle
from intake.services.pipeline import process_document
from intake.services.types import NormalizeError

from . import factories


def fake_fetch(content: bytes, *, final_url: str, content_type: str, status: int = 200):
    def _fetch(url, dest, **kwargs):
        dest.write(content)
        return FetchResult(final_url=final_url, http_status=status, content_type=content_type, size_bytes=len(content))

    return _fetch


class PipelineTestBase(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.temp_dir.name)
        self.override.enable()
        self.user = get_user_model().objects.create_user("intaker", "intaker@example.org", "pw")
        self.batch = IntakeBatch.objects.create(title="Batch", created_by=self.user)
        self.throttle = HostThrottle(0)

    def tearDown(self):
        self.override.disable()
        self.temp_dir.cleanup()

    def upload(self, name: str, content: bytes) -> SourceDocument:
        doc = SourceDocument.objects.create(
            batch=self.batch, origin=SourceDocument.Origin.UPLOAD, original_filename=name
        )
        doc.stored_file.save(name, ContentFile(content), save=True)
        return doc

    def url_doc(self, url: str) -> SourceDocument:
        return SourceDocument.objects.create(batch=self.batch, origin=SourceDocument.Origin.URL, source_url=url)


class ProcessDocumentTests(PipelineTestBase):
    def test_uploaded_pdf_becomes_ready_with_pages(self):
        doc = self.upload("report.pdf", factories.text_pdf())
        process_document(doc, throttle=self.throttle)
        doc.refresh_from_db()
        self.assertEqual(doc.status, SourceDocument.Status.READY)
        self.assertEqual(doc.kind, SourceDocument.Kind.PDF)
        self.assertEqual(len(doc.sha256), 64)
        self.assertGreaterEqual(doc.pages.count(), 1)
        page = doc.pages.first()
        self.assertTrue(page.preview_image)
        self.assertTrue(os.path.exists(page.preview_image.path))
        self.assertTrue(IntakeEvent.objects.filter(document=doc, action="normalized").exists())

    @mock.patch("intake.services.pipeline.fetch_bytes", return_value=(factories.png_bytes(), "image/png"))
    @mock.patch("intake.services.pipeline.fetch_to_file")
    def test_url_html_records_fetch_metadata_and_links(self, fetch_mock, _fetch_bytes):
        fetch_mock.side_effect = fake_fetch(
            factories.html_page(),
            final_url="https://example.org/reports/annual.html",
            content_type="text/html; charset=utf-8",
        )
        doc = self.url_doc("https://example.org/annual")
        throttle = mock.Mock(wraps=self.throttle)
        process_document(doc, throttle=throttle)
        doc.refresh_from_db()
        self.assertEqual(doc.status, SourceDocument.Status.READY, doc.error)
        self.assertEqual(doc.final_url, "https://example.org/reports/annual.html")
        self.assertEqual(doc.http_status, 200)
        self.assertIsNotNone(doc.fetched_at)
        self.assertEqual(doc.kind, "html")
        self.assertTrue(doc.stored_file.name.endswith(".html"))
        self.assertGreaterEqual(CandidateLink.objects.filter(document=doc).count(), 3)
        self.assertTrue(IntakeEvent.objects.filter(document=doc, action="fetched").exists())
        waited = [c.args[0] for c in throttle.wait.call_args_list]
        self.assertEqual(waited[0], "https://example.org/annual")
        self.assertIn("https://example.org/img/abs.png", waited)

    def test_png_is_rejected_with_message(self):
        doc = self.upload("report.pdf", factories.png_bytes())
        process_document(doc, throttle=self.throttle)
        doc.refresh_from_db()
        self.assertEqual(doc.status, SourceDocument.Status.REJECTED)
        self.assertEqual(doc.error, VALID_TYPES_MESSAGE)
        self.assertEqual(doc.pages.count(), 0)
        self.assertTrue(IntakeEvent.objects.filter(document=doc, action="rejected").exists())

    def test_duplicate_sha_sets_duplicate_of_and_warns(self):
        first = self.upload("a.pdf", factories.text_pdf())
        process_document(first, throttle=self.throttle)
        second = self.upload("b.pdf", factories.text_pdf())
        process_document(second, throttle=self.throttle)
        second.refresh_from_db()
        self.assertEqual(second.duplicate_of_id, first.pk)
        self.assertIn("duplicate", [w["code"] for w in second.warnings])
        self.assertEqual(second.status, SourceDocument.Status.READY)

    def test_normalizer_failure_rolls_back(self):
        doc = self.upload("report.pdf", factories.text_pdf())

        def broken_persist(document, result):
            DocumentPage.objects.create(document=document, number=1, text="partial")
            raise NormalizeError("Could not read this PDF")

        with mock.patch("intake.services.pipeline.persist_result", side_effect=broken_persist):
            process_document(doc, throttle=self.throttle)
        doc.refresh_from_db()
        self.assertEqual(doc.status, SourceDocument.Status.FAILED)
        self.assertEqual(doc.error, "Could not read this PDF")
        self.assertEqual(DocumentPage.objects.filter(document=doc).count(), 0)
        self.assertTrue(IntakeEvent.objects.filter(document=doc, action="failed").exists())

    @mock.patch("intake.services.pipeline.fetch_to_file")
    def test_retryable_fetch_error_propagates(self, fetch_mock):
        fetch_mock.side_effect = FetchError("Server error 503", retryable=True, http_status=503)
        doc = self.url_doc("https://example.org/report.pdf")
        with self.assertRaises(FetchError):
            process_document(doc, throttle=self.throttle)

    @mock.patch("intake.services.pipeline.fetch_to_file")
    def test_non_retryable_fetch_error_fails_without_partial_file(self, fetch_mock):
        def partial(url, dest, **kwargs):
            dest.write(b"%PDF-1.4 partial")
            raise FetchError("File exceeds size limit", retryable=False)

        fetch_mock.side_effect = partial
        doc = self.url_doc("https://example.org/report.pdf")
        process_document(doc, throttle=self.throttle)
        doc.refresh_from_db()
        self.assertEqual(doc.status, SourceDocument.Status.FAILED)
        self.assertEqual(doc.error, "File exceeds size limit")
        self.assertFalse(doc.stored_file)

    @mock.patch("intake.services.pipeline.fetch_to_file")
    def test_content_type_mismatch_warns(self, fetch_mock):
        fetch_mock.side_effect = fake_fetch(
            factories.text_pdf(), final_url="https://example.org/r.pdf", content_type="text/html"
        )
        doc = self.url_doc("https://example.org/r.pdf")
        process_document(doc, throttle=self.throttle)
        doc.refresh_from_db()
        self.assertEqual(doc.status, SourceDocument.Status.READY)
        self.assertIn("content_type_mismatch", [w["code"] for w in doc.warnings])
        self.assertTrue(doc.stored_file.name.endswith(".pdf"))

    def test_reprocessing_is_idempotent(self):
        doc = self.upload("report.pdf", factories.text_pdf())
        process_document(doc, throttle=self.throttle)
        pages = doc.pages.count()
        process_document(doc, throttle=self.throttle)
        doc.refresh_from_db()
        self.assertEqual(doc.status, SourceDocument.Status.READY)
        self.assertEqual(doc.pages.count(), pages)
