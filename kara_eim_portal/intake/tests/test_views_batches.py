import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from intake.models import IntakeBatch, IntakeEvent, ProcessingJob, SourceDocument
from intake.services.batches import parse_urls
from intake.services.detect import VALID_TYPES_MESSAGE
from intake.tests.factories import link_workbook, png_bytes, text_pdf

Status = SourceDocument.Status
User = get_user_model()

class ParseUrlsTests(TestCase):
    def test_parse_urls_strips_blanks_and_dedupes(self):
        text = "\n https://a.gov/x.pdf \n\nhttps://a.gov/x.pdf\nhttps://b.gov/\n"
        self.assertEqual(parse_urls(text), ["https://a.gov/x.pdf", "https://b.gov/"])

    def test_parse_urls_rejects_bad_line(self):
        with self.assertRaises(ValidationError) as ctx:
            parse_urls("https://a.gov/\nftp://x")
        self.assertIn("ftp://x", str(ctx.exception))


class BatchViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("admin", "a@example.org", "pw")

    def setUp(self):
        media = tempfile.mkdtemp(prefix="intake-views-")
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        self.client.force_login(self.admin)

    def _post(self, **extra):
        data = {"title": "Batch A", "default_scope": "Minnesota", "notes": "n", "urls": ""}
        data.update(extra)
        return self.client.post(reverse("intake:batch_new"), data)

    def _doc(self, status, batch=None):
        batch = batch or IntakeBatch.objects.create(title="B", created_by=self.admin)
        return SourceDocument.objects.create(
            batch=batch, origin="url", source_url="https://a.gov/x.pdf", status=status
        )

    def test_create_batch_with_files_and_urls_queues_jobs(self):
        resp = self._post(
            files=[
                SimpleUploadedFile("a.pdf", text_pdf()),
                SimpleUploadedFile("b.xlsx", link_workbook()),
            ],
            urls="https://a.gov/1.pdf\nhttps://a.gov/2.pdf",
        )
        batch = IntakeBatch.objects.get()
        self.assertRedirects(resp, reverse("intake:batch_detail", args=[batch.pk]))
        docs = list(batch.documents.all())
        self.assertEqual(len(docs), 4)
        self.assertTrue(all(d.status == Status.QUEUED for d in docs))
        self.assertTrue(all(d.scope == "Minnesota" for d in docs))
        self.assertEqual(ProcessingJob.objects.filter(state="pending").count(), 4)
        uploads = [d for d in docs if d.origin == "upload"]
        self.assertEqual(len(uploads), 2)
        for d, ext in zip(uploads, ["pdf", "xlsx"]):
            self.assertEqual(d.stored_file.name, f"intake/{batch.pk}/{d.pk}/original.{ext}")
        actions = list(batch.events.values_list("action", flat=True))
        self.assertEqual(actions.count("batch_created"), 1)
        self.assertEqual(actions.count("document_added"), 4)

    def test_upload_filename_with_spaces_and_unicode(self):
        name = "Rapport annuel – 2024 (final).pdf"
        self._post(files=[SimpleUploadedFile(name, text_pdf())])
        doc = SourceDocument.objects.get()
        self.assertEqual(doc.original_filename, name)
        self.assertEqual(doc.stored_file.name, f"intake/{doc.batch_id}/{doc.pk}/original.pdf")

    def test_png_upload_rejected_with_valid_types_message(self):
        resp = self._post(files=[SimpleUploadedFile("x.png", png_bytes())])
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, VALID_TYPES_MESSAGE)
        self.assertEqual(IntakeBatch.objects.count(), 0)

    def test_requires_file_or_url(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(IntakeBatch.objects.count(), 0)

    @override_settings(INTAKE_MAX_ITEMS_PER_BATCH=2)
    def test_too_many_items_rejected(self):
        resp = self._post(urls="https://a.gov/1\nhttps://a.gov/2\nhttps://a.gov/3")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "at most 2")
        self.assertEqual(IntakeBatch.objects.count(), 0)

    def test_status_json_reports_processing(self):
        doc = self._doc(Status.QUEUED)
        resp = self.client.get(reverse("intake:batch_status", args=[doc.batch_id]))
        data = resp.json()
        self.assertEqual(data["status"], "processing")
        self.assertEqual(
            data["documents"],
            [{"id": doc.pk, "status": "queued", "pages": 0, "tables": 0, "images": 0, "warnings": 0}],
        )

    def test_home_and_detail_render(self):
        doc = self._doc(Status.FAILED)
        self.assertContains(self.client.get(reverse("intake:home")), "B")
        self.assertContains(self.client.get(reverse("intake:batch_detail", args=[doc.batch_id])), "Retry")

    def test_retry_failed_document_requeues(self):
        doc = self._doc(Status.FAILED)
        ProcessingJob.objects.create(
            document=doc, state="gave_up", attempts=3, run_after=timezone.now(), last_error="boom"
        )
        doc.error = "boom"
        doc.save()
        resp = self.client.post(reverse("intake:document_retry", args=[doc.pk]))
        self.assertRedirects(resp, reverse("intake:batch_detail", args=[doc.batch_id]))
        doc.refresh_from_db()
        self.assertEqual(doc.status, Status.QUEUED)
        self.assertEqual(doc.error, "")
        job = doc.job
        self.assertEqual((job.state, job.attempts, job.last_error), ("pending", 0, ""))
        self.assertTrue(IntakeEvent.objects.filter(action="retried").exists())

    def test_retry_creates_missing_job_and_rejects_non_failed(self):
        doc = self._doc(Status.FAILED)
        self.client.post(reverse("intake:document_retry", args=[doc.pk]))
        self.assertEqual(doc.job.state, "pending")
        ready = self._doc(Status.READY)
        resp = self.client.post(reverse("intake:document_retry", args=[ready.pk]))
        self.assertEqual(resp.status_code, 400)

    def test_remove_document_deletes_and_logs(self):
        doc = self._doc(Status.FAILED)
        batch_id = doc.batch_id
        resp = self.client.post(reverse("intake:document_remove", args=[doc.pk]))
        self.assertRedirects(resp, reverse("intake:batch_detail", args=[batch_id]))
        self.assertFalse(SourceDocument.objects.filter(pk=doc.pk).exists())
        self.assertTrue(IntakeEvent.objects.filter(batch_id=batch_id, action="removed").exists())

    def test_remove_document_not_allowed_when_ready(self):
        doc = self._doc(Status.READY)
        resp = self.client.post(reverse("intake:document_remove", args=[doc.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(SourceDocument.objects.filter(pk=doc.pk).exists())

    def test_mutations_require_post(self):
        doc = self._doc(Status.FAILED)
        self.assertEqual(self.client.get(reverse("intake:document_retry", args=[doc.pk])).status_code, 405)

    def test_user_without_permission_gets_403_on_create(self):
        user = User.objects.create_user("plain", password="pw")
        self.client.force_login(user)
        self.assertEqual(self._post(urls="https://a.gov/1.pdf").status_code, 403)
        self.assertEqual(self.client.get(reverse("intake:home")).status_code, 403)
        self.assertEqual(IntakeBatch.objects.count(), 0)

    def test_anonymous_redirected_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("intake:home")).status_code, 302)
