"""End-to-end intake flow: upload through the form, run the worker, view results."""
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from intake.models import IntakeBatch, SourceDocument
from intake.services.detect import VALID_TYPES_MESSAGE
from intake.tests.factories import png_bytes, text_pdf

REPO_ROOT = Path(settings.BASE_DIR).parent
WORKBOOK = REPO_ROOT / "EIM Field Inventory & Data Tracking (1).xlsx"


@override_settings(INTAKE_HOST_DELAY=0)
class EndToEndTests(TestCase):
    def setUp(self):
        media = tempfile.mkdtemp(prefix="intake-e2e-")
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        admin = get_user_model().objects.create_superuser("admin", "a@example.org", "pw")
        self.client.force_login(admin)

    def _post(self, files):
        return self.client.post(
            reverse("intake:batch_new"),
            {"title": "E2E", "default_scope": "", "notes": "", "urls": "", "files": files},
        )

    def test_png_is_rejected_with_valid_types_message(self):
        resp = self._post([SimpleUploadedFile("chart.png", png_bytes())])
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, VALID_TYPES_MESSAGE)
        self.assertEqual(IntakeBatch.objects.count(), 0)

    def test_pdf_and_real_workbook_flow(self):
        if not WORKBOOK.exists():
            self.skipTest("EIM workbook not present in the repository root")
        rejected = self._post(
            [
                SimpleUploadedFile("a.pdf", text_pdf()),
                SimpleUploadedFile("x.xlsx", WORKBOOK.read_bytes()),
                SimpleUploadedFile("chart.png", png_bytes()),
            ]
        )
        self.assertContains(rejected, VALID_TYPES_MESSAGE)
        self.assertEqual(IntakeBatch.objects.count(), 0)

        resp = self._post(
            [
                SimpleUploadedFile("a.pdf", text_pdf()),
                SimpleUploadedFile("x.xlsx", WORKBOOK.read_bytes()),
            ]
        )
        batch = IntakeBatch.objects.get()
        self.assertRedirects(resp, reverse("intake:batch_detail", args=[batch.pk]))

        call_command("run_intake_worker", "--once")

        pdf = batch.documents.get(kind=SourceDocument.Kind.PDF)
        xlsx = batch.documents.get(kind=SourceDocument.Kind.XLSX)
        self.assertEqual(pdf.status, SourceDocument.Status.READY)
        self.assertTrue(pdf.pages.first().preview_image)
        self.assertEqual(xlsx.status, SourceDocument.Status.READY)
        self.assertIn("eim_workbook", [w["code"] for w in xlsx.warnings])
        # "Report Asset URL" is column D of the Source Assets sheet.
        labels = set(xlsx.candidate_links.values_list("label", flat=True))
        self.assertIn("Source Assets!D5", labels)
        self.assertTrue(xlsx.candidate_links.filter(label__startswith="Source Assets!D", url__startswith="http").exists())

        self.assertEqual(self.client.get(reverse("intake:batch_detail", args=[batch.pk])).status_code, 200)
        for doc in (pdf, xlsx):
            self.assertEqual(self.client.get(reverse("intake:document_detail", args=[doc.pk])).status_code, 200)
