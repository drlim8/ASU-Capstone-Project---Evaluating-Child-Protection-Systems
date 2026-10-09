from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from intake.models import CandidateLink, DocumentPage, IntakeBatch, SourceDocument


class IntakeModelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("intake-user", password="x")
        self.batch = IntakeBatch.objects.create(title="Batch", created_by=self.user)

    def _doc(self, status, **kwargs):
        return SourceDocument.objects.create(
            batch=self.batch,
            kind=SourceDocument.Kind.PDF,
            origin=SourceDocument.Origin.UPLOAD,
            status=status,
            **kwargs,
        )

    def test_batch_status_processing_when_any_document_active(self):
        self._doc(SourceDocument.Status.READY)
        self._doc(SourceDocument.Status.QUEUED)
        self.assertEqual(self.batch.status, "processing")

    def test_batch_status_has_problems_when_failed_or_rejected(self):
        self._doc(SourceDocument.Status.READY)
        self._doc(SourceDocument.Status.REJECTED)
        self.assertEqual(self.batch.status, "has_problems")

    def test_batch_status_ready_when_all_ready(self):
        self._doc(SourceDocument.Status.READY)
        self._doc(SourceDocument.Status.READY)
        self.assertEqual(self.batch.status, "ready")

    def test_page_number_unique_per_document(self):
        doc = self._doc(SourceDocument.Status.READY)
        DocumentPage.objects.create(document=doc, number=1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DocumentPage.objects.create(document=doc, number=1)

    def test_candidate_link_unique_per_document_url(self):
        doc = self._doc(SourceDocument.Status.READY)
        CandidateLink.objects.create(document=doc, url="https://example.org/a.pdf")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CandidateLink.objects.create(document=doc, url="https://example.org/a.pdf")
