import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from imports.models import DatasetImport, PublishedRecord

from .workbook_factory import build_test_workbook


class ImportWorkflowTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.temp_dir.name)
        self.override.enable()
        self.user = get_user_model().objects.create_superuser("reviewer", "reviewer@example.org", "test-password")
        self.client.force_login(self.user)

    def tearDown(self):
        self.override.disable()
        self.temp_dir.cleanup()

    def test_upload_validate_and_approve(self):
        upload = SimpleUploadedFile(
            "test-eim.xlsx",
            build_test_workbook(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response = self.client.post(
            reverse("imports:upload"),
            {
                "title": "Test EIM import",
                "jurisdiction": "Minnesota",
                "reporting_year": 2024,
                "framework_version": "1.2",
                "uploaded_file": upload,
            },
        )
        dataset_import = DatasetImport.objects.get()
        self.assertRedirects(response, reverse("imports:detail", args=[dataset_import.pk]))
        self.assertEqual(dataset_import.status, DatasetImport.Status.READY)
        self.assertEqual(dataset_import.staged_records.count(), 6)
        detail = self.client.get(reverse("imports:detail", args=[dataset_import.pk]))
        self.assertContains(detail, "Validation results")
        self.assertContains(detail, "Data groupings")
        self.assertContains(detail, "Approve and publish")

        response = self.client.post(reverse("imports:approve", args=[dataset_import.pk]), {"notes": "Reviewed"})
        dataset_import.refresh_from_db()
        self.assertRedirects(response, reverse("imports:detail", args=[dataset_import.pk]))
        self.assertEqual(dataset_import.status, DatasetImport.Status.APPROVED)
        self.assertEqual(PublishedRecord.objects.count(), 6)
        published = self.client.get(reverse("imports:published"))
        self.assertContains(published, "Test EIM import")
