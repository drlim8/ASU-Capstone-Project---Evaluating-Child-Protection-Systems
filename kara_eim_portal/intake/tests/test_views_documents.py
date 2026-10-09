import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from intake.models import CandidateLink, IntakeBatch, IntakeEvent, ProcessingJob, SourceDocument
from intake.services.persist import persist_result
from intake.services.types import ImageData, LinkData, NormalizedResult, PageData, TableData
from intake.tests.factories import png_bytes

User = get_user_model()
Status = SourceDocument.Status


class DocumentViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser("admin", "a@example.org", "pw")
        cls.viewer = User.objects.create_user("viewer", password="pw")
        cls.viewer.user_permissions.add(Permission.objects.get(codename="view_intakebatch"))

    def setUp(self):
        media = tempfile.mkdtemp(prefix="intake-docs-")
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        self.client.force_login(self.admin)
        self.batch = IntakeBatch.objects.create(title="B", created_by=self.admin)

    def _doc(self, **kw):
        defaults = dict(batch=self.batch, origin="upload", original_filename="r.xlsx",
                        kind="xlsx", status=Status.READY, scope="Minnesota")
        defaults.update(kw)
        return SourceDocument.objects.create(**defaults)

    def _links(self, doc, n=3):
        return [CandidateLink.objects.create(document=doc, url=f"https://a.gov/{i}.pdf", label=f"L{i}")
                for i in range(n)]

    def test_detail_renders_pages_tables_images(self):
        doc = self._doc(warnings=[{"code": "w1", "message": "Careful <b>x</b>", "page": None}])
        result = NormalizedResult(
            pages=[PageData(
                number=1, text="Foster care total 7763", preview_png=png_bytes(),
                tables=[TableData(index=0, rows=[["Year", "Count"], ["2023", None]])],
                images=[
                    ImageData(index=0, content=png_bytes(200, 150), ext="png", width=200, height=150, alt="Big chart"),
                    ImageData(index=1, content=png_bytes(10, 10), ext="png", width=10, height=10),
                ],
            )],
            links=[LinkData(url="https://a.gov/x.pdf", label="X")], warnings=[],
        )
        persist_result(doc, result)
        page = doc.pages.get()
        resp = self.client.get(reverse("intake:document_detail", args=[doc.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Foster care total 7763")
        self.assertContains(resp, "<table")
        self.assertContains(resp, f'<img src="{page.preview_image.url}"')
        self.assertContains(resp, "Big chart")
        self.assertContains(resp, "Show 1 small/decorative images")
        self.assertContains(resp, "w1")
        self.assertNotContains(resp, "Careful <b>x</b>")
        self.assertContains(resp, "https://a.gov/x.pdf")
        html = resp.content.decode()
        marker = html.index("Show 1 small/decorative images")
        self.assertIn("<details", html[:marker][-200:])

    def test_detail_requires_view_permission(self):
        doc = self._doc()
        self.client.force_login(User.objects.create_user("nobody", password="pw"))
        self.assertEqual(self.client.get(reverse("intake:document_detail", args=[doc.pk])).status_code, 403)

    def test_post_requires_add_permission(self):
        doc = self._doc()
        self.client.force_login(self.viewer)
        url = reverse("intake:document_detail", args=[doc.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {"scope": "X", "year_hint": ""}).status_code, 403)
        doc.refresh_from_db()
        self.assertEqual(doc.scope, "Minnesota")

    def test_edit_scope_and_year_hint(self):
        doc = self._doc()
        url = reverse("intake:document_detail", args=[doc.pk])
        resp = self.client.post(url, {"scope": "National", "year_hint": "2023"})
        self.assertRedirects(resp, url)
        doc.refresh_from_db()
        self.assertEqual((doc.scope, doc.year_hint), ("National", 2023))
        resp = self.client.post(url, {"scope": "National", "year_hint": "1800"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["form"].errors["year_hint"])
        doc.refresh_from_db()
        self.assertEqual(doc.year_hint, 2023)

    def test_queue_selected_links_creates_children(self):
        doc = self._doc()
        links = self._links(doc)
        resp = self.client.post(
            reverse("intake:document_queue_links", args=[doc.pk]), {"link_ids": [links[0].pk, links[2].pk]}
        )
        self.assertRedirects(resp, reverse("intake:document_detail", args=[doc.pk]))
        children = list(doc.children.order_by("id"))
        self.assertEqual(len(children), 2)
        self.assertEqual([c.source_url for c in children], [links[0].url, links[2].url])
        for c in children:
            self.assertEqual(c.origin, "from_workbook")
            self.assertEqual(c.parent, doc)
            self.assertEqual(c.batch, self.batch)
            self.assertEqual(c.scope, "Minnesota")
            self.assertEqual(c.status, Status.QUEUED)
            self.assertEqual(c.kind, "unknown")
            self.assertEqual(c.job.state, ProcessingJob.State.PENDING)
        links[0].refresh_from_db()
        links[1].refresh_from_db()
        self.assertEqual(links[0].queued_as, children[0])
        self.assertIsNone(links[1].queued_as)
        ev = IntakeEvent.objects.get(action="links_queued")
        self.assertEqual(ev.document, doc)
        self.assertEqual(ev.details["children"], [c.pk for c in children])

    def test_from_page_origin_for_non_xlsx(self):
        doc = self._doc(kind="html")
        link = self._links(doc, 1)[0]
        self.client.post(reverse("intake:document_queue_links", args=[doc.pk]), {"link_ids": [link.pk]})
        self.assertEqual(doc.children.get().origin, "from_page")

    def test_queue_same_link_twice_creates_one_child(self):
        doc = self._doc()
        link = self._links(doc, 1)[0]
        url = reverse("intake:document_queue_links", args=[doc.pk])
        self.client.post(url, {"link_ids": [link.pk]})
        self.client.post(url, {"link_ids": [link.pk]})
        self.assertEqual(doc.children.count(), 1)
        self.assertEqual(ProcessingJob.objects.count(), 1)

    def test_cannot_queue_links_from_other_document(self):
        doc, other = self._doc(), self._doc(original_filename="o.xlsx")
        link = self._links(other, 1)[0]
        self.client.post(reverse("intake:document_queue_links", args=[doc.pk]), {"link_ids": [link.pk]})
        self.assertEqual(SourceDocument.objects.filter(parent__isnull=False).count(), 0)
        link.refresh_from_db()
        self.assertIsNone(link.queued_as)

    def test_queue_ignores_garbage_ids_and_requires_permission(self):
        doc = self._doc()
        url = reverse("intake:document_queue_links", args=[doc.pk])
        resp = self.client.post(url, {"link_ids": ["abc", ""]})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(doc.children.count(), 0)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(url, {"link_ids": []}).status_code, 403)

    def test_download_original_uses_original_filename(self):
        doc = self._doc(original_filename="report 2023.xlsx")
        doc.stored_file.save("x.xlsx", ContentFile(b"abc"), save=True)
        resp = self.client.get(reverse("intake:document_original", args=[doc.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("report 2023.xlsx", resp["Content-Disposition"])
        self.assertIn("attachment", resp["Content-Disposition"])
        b"".join(resp.streaming_content)
        resp.close()

    def test_download_original_404_without_file(self):
        doc = self._doc(origin="url", original_filename="")
        self.assertEqual(self.client.get(reverse("intake:document_original", args=[doc.pk])).status_code, 404)

    def test_batch_detail_links_to_document(self):
        doc = self._doc()
        resp = self.client.get(reverse("intake:batch_detail", args=[self.batch.pk]))
        self.assertContains(resp, reverse("intake:document_detail", args=[doc.pk]))
