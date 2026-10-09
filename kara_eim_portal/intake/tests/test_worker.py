import tempfile
from io import StringIO
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from intake.models import IntakeBatch, ProcessingJob, SourceDocument
from intake.services.fetch import FetchError, HostThrottle
from intake.services.worker import claim_next_job, run_job, run_once


class WorkerTestBase(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.temp_dir.name)
        self.override.enable()
        self.user = get_user_model().objects.create_user("worker", "worker@example.org", "pw")
        self.batch = IntakeBatch.objects.create(title="Batch", created_by=self.user)
        self.throttle = HostThrottle(0)

    def tearDown(self):
        self.override.disable()
        self.temp_dir.cleanup()

    def make_job(self, **job_fields) -> ProcessingJob:
        doc = SourceDocument.objects.create(
            batch=self.batch, origin=SourceDocument.Origin.URL, source_url="https://example.org/r.pdf"
        )
        job_fields.setdefault("run_after", timezone.now() - timedelta(seconds=1))
        return ProcessingJob.objects.create(document=doc, **job_fields)


@mock.patch("intake.services.worker.process_document")
class WorkerTests(WorkerTestBase):
    def test_claims_pending_job_and_marks_done(self, process_mock):
        job = self.make_job()
        self.assertTrue(run_once(self.throttle))
        job.refresh_from_db()
        self.assertEqual(job.state, ProcessingJob.State.DONE)
        self.assertEqual(job.attempts, 1)
        process_mock.assert_called_once()

    def test_skips_job_with_future_run_after(self, process_mock):
        self.make_job(run_after=timezone.now() + timedelta(minutes=5))
        self.assertFalse(run_once(self.throttle))
        self.assertIsNone(claim_next_job())
        process_mock.assert_not_called()

    def test_retry_backoff_then_gives_up(self, process_mock):
        process_mock.side_effect = FetchError("Server error 503", retryable=True, http_status=503)
        job = self.make_job()

        before = timezone.now()
        self.assertTrue(run_once(self.throttle))
        job.refresh_from_db()
        self.assertEqual(job.state, ProcessingJob.State.PENDING)
        self.assertAlmostEqual((job.run_after - before).total_seconds(), 30, delta=5)
        self.assertIn("503", job.last_error)

        ProcessingJob.objects.filter(pk=job.pk).update(run_after=timezone.now())
        before = timezone.now()
        self.assertTrue(run_once(self.throttle))
        job.refresh_from_db()
        self.assertEqual(job.state, ProcessingJob.State.PENDING)
        self.assertAlmostEqual((job.run_after - before).total_seconds(), 120, delta=5)

        ProcessingJob.objects.filter(pk=job.pk).update(run_after=timezone.now())
        self.assertTrue(run_once(self.throttle))
        job.refresh_from_db()
        self.assertEqual(job.state, ProcessingJob.State.GAVE_UP)
        self.assertEqual(job.attempts, 3)
        job.document.refresh_from_db()
        self.assertEqual(job.document.status, SourceDocument.Status.FAILED)
        self.assertIn("503", job.document.error)

    def test_reclaims_stale_running_job(self, process_mock):
        now = timezone.now()
        stale = self.make_job(state=ProcessingJob.State.RUNNING, locked_at=now - timedelta(minutes=11))
        self.make_job(state=ProcessingJob.State.RUNNING, locked_at=now - timedelta(minutes=5))
        claimed = claim_next_job(now)
        self.assertEqual(claimed.pk, stale.pk)
        self.assertEqual(claimed.state, ProcessingJob.State.RUNNING)
        self.assertEqual(claimed.locked_at, now)
        self.assertIsNone(claim_next_job(now))

    def test_document_deleted_mid_job_does_not_crash(self, process_mock):
        self.make_job()
        job = claim_next_job()
        job.document.delete()
        run_job(job, self.throttle)
        self.assertEqual(SourceDocument.objects.count(), 0)
        self.assertEqual(ProcessingJob.objects.count(), 0)

    def test_unexpected_exception_gives_up_and_fails_document(self, process_mock):
        process_mock.side_effect = IndexError("list index out of range")
        job = self.make_job()
        with self.assertLogs("intake.services.worker", level="ERROR"):
            self.assertTrue(run_once(self.throttle))
        job.refresh_from_db()
        self.assertEqual(job.state, ProcessingJob.State.GAVE_UP)
        self.assertIn("Traceback", job.last_error)
        job.document.refresh_from_db()
        self.assertEqual(job.document.status, SourceDocument.Status.FAILED)
        self.assertTrue(job.document.error)

    def test_command_once_drains_queue(self, process_mock):
        a = self.make_job()
        b = self.make_job()
        call_command("run_intake_worker", "--once", stdout=StringIO())
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.state, ProcessingJob.State.DONE)
        self.assertEqual(b.state, ProcessingJob.State.DONE)
