# Batch Intake & Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `intake` Django app that accepts batches of PDFs, XLSX workbooks, and URLs. A background worker normalizes each one into pages, tables, and images for later extraction and review.

**Architecture:** Format-specific normalizers are pure Python and return a `NormalizedResult` dataclass. A persistence layer writes that result to Django models inside one transaction. A database-backed `ProcessingJob` queue is drained by a `run_intake_worker` management command. Views are server-rendered and extend the existing `imports/base.html`.

**Tech Stack:** Django 5.2, Python 3.14, openpyxl, pdfplumber, pypdfium2, requests, beautifulsoup4, Pillow; reportlab (tests only).

**Spec:** `kara_eim_portal/docs/superpowers/specs/2026-10-09-batch-intake-normalization-design.md`

All paths below are relative to `kara_eim_portal/`. Run tests with `.venv/Scripts/python.exe manage.py test intake -v 2` (Windows) or `python manage.py test intake -v 2`.

## Global Constraints

- Do not modify the `imports` app's models, services, or views. The only allowed change to `imports` is adding a nav link in `imports/templates/imports/base.html`.
- Rejection message, verbatim: `Valid file types are Excel (.xlsx), PDF, or a URL linking to one of these.`
- Blank cells are stored as `None`/`null`, never `0` or `""`.
- Limits (settings, with these defaults): `INTAKE_MAX_BYTES = 50 * 1024 * 1024`, `INTAKE_FETCH_TIMEOUT = 30`, `INTAKE_HOST_DELAY = 2.0`, `INTAKE_USER_AGENT = "KARA-EIM-Intake/1.0"`, `INTAKE_MAX_ITEMS_PER_BATCH = 100`. Per-image limit is 10 MB; at most 100 images per HTML page; at most 5 redirects.
- Retry: at most 3 attempts, only for network errors and HTTP 5xx, with backoff of 30 s and then 120 s. Stale lock threshold is 10 minutes.
- Decorative image: width < 64 or height < 64 or width × height < 10,000.
- `has_text_layer` is False when the page has < 20 non-whitespace characters. `js_rendered` warning when HTML text is < 200 characters and the page has `<script>`.
- Tests never touch the network. Mock `requests` and `socket.getaddrinfo`.
- Do not use PyMuPDF/fitz (AGPL).
- Dependency pins follow the existing `>=X,<Y` style in `requirements.txt`.

## Review Focus

1. **The URL textarea contains blank lines, surrounding whitespace, or the same URL twice.** Expected: blank lines are ignored, whitespace is stripped, and duplicates within one submission produce one document. → test in Task 8.
2. **HTML `<img>` uses relative paths, protocol-relative `//` paths, or `data:` URIs.** Expected: relative paths resolve against the page URL; `data:` images are decoded inline without a fetch; neither crashes the document. → test in Task 6.
3. **A workbook has formula cells (e.g. `=SUM(A1:A3)`).** Expected: the cached value is stored, not the formula text. A formula with no cached value is stored as `None`. → test in Task 5.
4. **A document is removed while the worker holds its job.** Expected: the worker finishes or aborts without crashing and does not recreate the document. → test in Task 7.
5. **"Queue selected" is clicked twice for the same link (double submit).** Expected: only one child document is created. → test in Task 9.

---

### Task 1: App scaffold, models, settings, dependencies

**Files:**
- Create: `intake/__init__.py`, `intake/apps.py`, `intake/models.py`, `intake/admin.py`, `intake/migrations/__init__.py`, `intake/tests/__init__.py`, `intake/tests/test_models.py`, `requirements-dev.txt`
- Modify: `config/settings.py` (add `"intake"` to `INSTALLED_APPS`; add the `INTAKE_*` settings from Global Constraints, each overridable with `os.environ`), `requirements.txt`

**Interfaces:**
- Produces models exactly as in spec §3 (`IntakeBatch`, `SourceDocument`, `CandidateLink`, `DocumentPage`, `ExtractedTable`, `ExtractedImage`, `ProcessingJob`, `IntakeEvent`). Choice enums are nested `TextChoices`, matching `DatasetImport.Status`:
  - `SourceDocument.Kind` (PDF, XLSX, HTML, UNKNOWN), `SourceDocument.Origin` (UPLOAD, URL, FROM_WORKBOOK, FROM_PAGE), `SourceDocument.Status` (QUEUED, FETCHING, NORMALIZING, READY, REJECTED, FAILED), `ProcessingJob.State` (PENDING, RUNNING, DONE, GAVE_UP)
- `IntakeBatch.status -> str` property returning `"processing" | "has_problems" | "ready"`
- `SourceDocument.ACTIVE_STATUSES = {QUEUED, FETCHING, NORMALIZING}`
- `document_upload_path(instance, filename)` → `intake/<batch_id>/<doc_id>/original.<ext>`. Page previews go to `.../pages/<n>.png`; images go to `.../images/<page>-<index>.<ext>`. Upload paths need `instance.pk`, so the document row is saved before the file is attached.

- [ ] **Step 1: Write the failing tests** in `intake/tests/test_models.py`:
  - `test_batch_status_processing_when_any_document_active` — one READY and one QUEUED → `"processing"`
  - `test_batch_status_has_problems_when_failed_or_rejected` — READY and REJECTED → `"has_problems"`
  - `test_batch_status_ready_when_all_ready` — all READY → `"ready"`
  - `test_page_number_unique_per_document` — creating a second `DocumentPage(number=1)` for the same document raises `IntegrityError`
  - `test_candidate_link_unique_per_document_url` — same (document, url) twice raises `IntegrityError`
- [ ] **Step 2: Run** `manage.py test intake.tests.test_models` → FAIL (`ModuleNotFoundError: intake`)
- [ ] **Step 3: Implement** the app, models, and admin registrations (list_display on status fields). Run `manage.py makemigrations intake`. Add to `requirements.txt`: `pdfplumber>=0.11,<0.12`, `pypdfium2>=4.30,<5`, `requests>=2.32,<3`, `beautifulsoup4>=4.12,<5`, `Pillow>=11,<12`. Create `requirements-dev.txt` with `-r requirements.txt` and `reportlab>=4.2,<5`. Install it with `pip install -r requirements-dev.txt` (adjust a pin's upper bound only if no wheel exists for Python 3.14, and note the change in the commit).
- [ ] **Step 4: Run** `manage.py test intake.tests.test_models` → PASS. Also run `manage.py test imports` → PASS, to confirm nothing regressed.
- [ ] **Step 5: Commit** `feat(intake): add intake app models and settings`

---

### Task 2: Shared result types and type detection

**Files:**
- Create: `intake/services/__init__.py`, `intake/services/types.py`, `intake/services/detect.py`, `intake/tests/factories.py`, `intake/tests/test_detect.py`

**Interfaces:**
- Produces in `types.py` (dataclasses, no Django imports):
  - `IntakeWarning(code: str, message: str, page: int | None = None)`
  - `TableData(index: int, rows: list[list[str | None]], bbox: list[float] | None = None, header_guess: list[str | None] | None = None)`
  - `ImageData(index: int, content: bytes, ext: str, width: int, height: int, bbox: list[float] | None = None, src: str = "", alt: str = "", caption: str = "")` with property `decorative -> bool`
  - `PageData(number: int, text: str, has_text_layer: bool = True, label: str = "", preview_png: bytes | None = None, tables: list[TableData] = [], images: list[ImageData] = [])` (use `field(default_factory=list)`)
  - `LinkData(url: str, label: str, context: str = "")`
  - `NormalizedResult(pages: list[PageData], links: list[LinkData], warnings: list[IntakeWarning])`
  - `class NormalizeError(Exception)` — a non-retryable failure whose message is shown to users
  - `is_decorative(width: int, height: int) -> bool`
- Produces in `detect.py`:
  - `VALID_TYPES_MESSAGE` (verbatim string from Global Constraints)
  - `detect_kind(path: Path) -> Literal["pdf", "xlsx", "html", "image", "unknown"]`, using the rules in spec §5.3. For XLSX, use `zipfile.is_zipfile` plus a namelist that contains `[Content_Types].xml` and a name starting with `xl/`.
  - `content_type_mismatch(kind: str, content_type: str) -> bool` — True only when `content_type` is non-empty and contradicts `kind` (ignore parameters like `; charset=`; treat `application/octet-stream` as no opinion)
- Produces in `tests/factories.py` (used by later tasks): `text_pdf(text="Foster care total 7763", with_table=True) -> bytes` (reportlab), `image_only_pdf() -> bytes` (Pillow RGB image saved as PDF), `pdf_with_image() -> bytes` (reportlab text plus a 200×150 drawn PNG), `png_bytes(w=200, h=150) -> bytes`, `link_workbook() -> bytes` (sheet "Source Assets" with header row `["Report Asset ID", "Report Asset URL", "Notes"]`, two URL rows, one blank cell, and one formula cell), `html_page(...) -> bytes` (table, `<img>` absolute, relative, and `data:`, links to `.pdf`/`.xlsx`/`.xls`, nav/script noise), `js_only_html() -> bytes`, `docx_bytes() -> bytes` (zip with `word/document.xml`).

- [ ] **Step 1: Write the failing tests** in `test_detect.py` (write the factory bytes to temp files):
  - `test_detects_pdf`, `test_detects_xlsx`, `test_detects_html_with_bom_and_leading_whitespace` → `"pdf"`, `"xlsx"`, `"html"`
  - `test_png_and_jpeg_are_image`, `test_docx_zip_is_unknown`, `test_random_bytes_unknown`
  - `test_content_type_mismatch` — `("pdf", "text/html; charset=utf-8")` → True; `("pdf", "application/pdf")` → False; `("pdf", "")` → False; `("pdf", "application/octet-stream")` → False
  - `test_is_decorative` — `(63, 500)` True; `(99, 100)` True (area 9,900); `(100, 100)` False
- [ ] **Step 2: Run** `manage.py test intake.tests.test_detect` → FAIL (import error)
- [ ] **Step 3: Implement** `types.py`, `detect.py`, and `factories.py`
- [ ] **Step 4: Run** `manage.py test intake.tests.test_detect` → PASS
- [ ] **Step 5: Commit** `feat(intake): add normalized result types and file type detection`

---

### Task 3: URL fetcher with SSRF guard, limits, and host throttle

**Files:**
- Create: `intake/services/fetch.py`, `intake/tests/test_fetch.py`

**Interfaces:**
- `class FetchError(Exception)` with attributes `retryable: bool` and `http_status: int | None`
- `class BlockedURLError(FetchError)` — always `retryable=False`
- `assert_public_url(url: str) -> None` — raises `BlockedURLError` for a non-http(s) scheme, a missing host, or any `socket.getaddrinfo` result that is private, loopback, link-local, reserved, multicast, or unspecified (`ipaddress` module)
- `FetchResult(final_url: str, http_status: int, content_type: str, size_bytes: int)` dataclass
- `fetch_to_file(url: str, dest: BinaryIO, *, max_bytes: int, timeout: float, user_agent: str, max_redirects: int = 5, session: requests.Session | None = None) -> FetchResult`. Use `allow_redirects=False` and follow `Location` manually, calling `assert_public_url` on every hop. Use `stream=True` and `iter_content(64 * 1024)`, and abort with `FetchError(retryable=False)` once more than `max_bytes` has been written. Error mapping: 4xx → `retryable=False`; 5xx and `requests.ConnectionError`/`Timeout` → `retryable=True`; too many redirects → `retryable=False`.
- `fetch_bytes(url: str, *, max_bytes: int, timeout: float, user_agent: str) -> tuple[bytes, str]` → `(content, content_type)`, built on `fetch_to_file` with a `BytesIO`
- `class HostThrottle(delay: float, clock=time.monotonic, sleep=time.sleep)` with `wait(url: str) -> None`. It sleeps only enough that consecutive calls for the same hostname are at least `delay` apart.

- [ ] **Step 1: Write the failing tests** (patch `intake.services.fetch.socket.getaddrinfo` and pass a `Mock` session whose `.get` returns fake responses with `status_code`, `headers`, `iter_content`, and `close`):
  - `test_blocks_non_http_scheme` (`file:///etc/passwd`, `ftp://x`)
  - `test_blocks_private_and_loopback` (127.0.0.1, 10.0.0.5, 169.254.169.254, ::1)
  - `test_blocks_redirect_to_private_ip` — the first hop resolves public and returns 302 to `http://internal/`, which resolves to 10.0.0.1 → `BlockedURLError`
  - `test_follows_redirect_and_records_final_url` — `result.final_url` is the second URL and `http_status == 200`
  - `test_too_many_redirects` — six 302s → `FetchError`, `retryable is False`
  - `test_aborts_over_max_bytes` — chunks totaling `max_bytes + 1` → `FetchError`, not retryable
  - `test_4xx_not_retryable_5xx_retryable` — 404 → `retryable False`, `http_status == 404`; 503 → `retryable True`
  - `test_timeout_is_retryable` — `session.get` raises `requests.Timeout` → `retryable True`
  - `test_sends_user_agent` — `session.get` was called with `headers["User-Agent"] == "KARA-EIM-Intake/1.0"`
  - `test_host_throttle_waits_only_for_same_host` — fake clock: `wait("https://a.gov/1")`, then at t=0.5 `wait("https://a.gov/2")` → sleep called with 1.5; `wait("https://b.gov/")` → no sleep
- [ ] **Step 2: Run** `manage.py test intake.tests.test_fetch` → FAIL
- [ ] **Step 3: Implement** `fetch.py`
- [ ] **Step 4: Run** `manage.py test intake.tests.test_fetch` → PASS
- [ ] **Step 5: Commit** `feat(intake): add guarded URL fetcher and per-host throttle`

---

### Task 4: PDF normalizer

**Files:**
- Create: `intake/services/normalizers/__init__.py`, `intake/services/normalizers/pdf.py`, `intake/tests/test_normalize_pdf.py`

**Interfaces:**
- Consumes the `types.py` dataclasses and `NormalizeError` (Task 2)
- Produces `normalize_pdf(path: Path) -> NormalizedResult`
  - Text and tables come from `pdfplumber` (`page.extract_text() or ""`, `page.find_tables()` → `.extract()` rows and `.bbox`). Empty strings in table cells become `None`.
  - Embedded images and the page preview come from `pypdfium2`: `page.render(scale=150/72).to_pil()` → PNG bytes for `preview_png`, and `page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE])` → `obj.get_bitmap().to_pil()` saved as PNG, with `obj.get_pos()` as the bbox.
  - Encrypted or corrupt input (`pdfplumber`/`pypdfium2` raising `PdfiumError`, `PDFSyntaxError`, or `PDFPasswordIncorrect`) → `NormalizeError("This PDF is password-protected or damaged and could not be read.")`
  - `header_guess` = the first table row

- [ ] **Step 1: Write the failing tests** using factories:
  - `test_text_pdf_has_text_table_and_preview` — 1 page; `"7763" in page.text`; `has_text_layer`; ≥1 table; table rows include a `None` for the factory's blank cell; `preview_png` starts with `b"\x89PNG"`
  - `test_pdf_with_image_extracts_image_with_bbox` — `len(page.images) == 1`; width ≈ 200; `bbox` has 4 floats; `decorative is False`
  - `test_image_only_pdf_warns_no_text_layer` — `has_text_layer is False`; `result.warnings` contains code `"no_text_layer"`; the page image is still saved (preview present)
  - `test_corrupt_pdf_raises_normalize_error` — bytes `b"%PDF-1.4 garbage"` → `NormalizeError`
- [ ] **Step 2: Run** `manage.py test intake.tests.test_normalize_pdf` → FAIL
- [ ] **Step 3: Implement** `normalize_pdf`
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat(intake): add PDF normalizer`

---

### Task 5: XLSX normalizer

**Files:**
- Create: `intake/services/normalizers/xlsx.py`, `intake/tests/test_normalize_xlsx.py`

**Interfaces:**
- Consumes the Task 2 types; `imports.services.schema.CORE_SHEETS` (read-only import)
- Produces `normalize_xlsx(path: Path) -> NormalizedResult`
  - Load with `openpyxl.load_workbook(path, data_only=True)`; not read-only, because `ws._images` needs full mode. One `PageData` per worksheet, with `label` set to the sheet title. One `TableData` covers `ws.iter_rows(values_only=True)` trimmed to the used range. Values are converted to `str`; `None` stays `None`. Page `text` is the non-null cells joined by tabs and newlines.
  - Images: `for img in ws._images` → `img._data()` bytes, `ext="png"` unless the bytes say otherwise, and size read with Pillow. Wrap this in try/except, and add a warning `image_unreadable` when it fails.
  - URL columns are detected as in spec §6.2. One `LinkData` per distinct URL, with `label=f"{sheet}!{cell.coordinate}"` and `context` = the header text.
  - If every name in `CORE_SHEETS` is a sheet title, add `IntakeWarning("eim_workbook", "This looks like an EIM workbook. You can also load it through the EIM import.")`
  - `zipfile.BadZipFile` or an openpyxl `InvalidFileException` → `NormalizeError("This workbook is damaged and could not be read.")`

- [ ] **Step 1: Write the failing tests**:
  - `test_each_sheet_is_a_page_with_table` — `pages[0].label == "Source Assets"`, and `tables[0].rows[0] == ["Report Asset ID", "Report Asset URL", "Notes"]`
  - `test_blank_cells_are_none_not_zero` — the factory's blank cell → `None`
  - `test_formula_cell_uses_cached_value_or_none` (Review Focus 3) — a formula cell saved by openpyxl has no cached value → `None`, and the text `"=SUM"` does not appear in any row
  - `test_url_column_produces_candidate_links` — 2 links; `label == "Source Assets!B2"`; `context == "Report Asset URL"`
  - `test_url_column_detected_by_content_without_header` — header `"Where"` with 3 of 4 cells being URLs → links produced
  - `test_eim_workbook_warning` — `imports.tests.workbook_factory.build_test_workbook()` → warning code `"eim_workbook"`
  - `test_damaged_workbook_raises` — truncated xlsx bytes → `NormalizeError`
- [ ] **Step 2: Run** `manage.py test intake.tests.test_normalize_xlsx` → FAIL
- [ ] **Step 3: Implement** `normalize_xlsx`
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat(intake): add XLSX normalizer with URL-column detection`

---

### Task 6: HTML normalizer

**Files:**
- Create: `intake/services/normalizers/html.py`, `intake/tests/test_normalize_html.py`

**Interfaces:**
- Consumes the Task 2 types; `FetchError` (Task 3)
- Produces `normalize_html(path: Path, base_url: str, fetch_image: Callable[[str], tuple[bytes, str]]) -> NormalizedResult`
  - Parse with `BeautifulSoup(raw, "html.parser")`; the encoding comes from bs4.
  - Tables and images are collected **before** removing `script, style, nav, header, footer, noscript`. The text is `soup.get_text("\n")` with whitespace runs collapsed. The result is always exactly one page (`number=1`).
  - Tables: expand `colspan`/`rowspan`; empty cells become `None`.
  - Images: resolve `src` with `urljoin(base_url, src)`. A `data:` URI is decoded with `base64`/`urllib.parse.unquote` and never passed to `fetch_image`. Any other URL goes to `fetch_image`; a `FetchError` there adds a warning `image_fetch_failed` with the `src`, and processing continues. Stop after 100 images and add a warning `image_limit`. Width and height come from Pillow. A `<figure>`'s `<figcaption>` text becomes the `caption` of the images inside it.
  - Links: resolve `href`; keep a link when the path (lowercased, query string dropped) ends in `.pdf`/`.xlsx`/`.xls`, or the link text contains "download". De-duplicate by URL. `label` = the link text stripped, or the URL.
  - `js_rendered` warning per Global Constraints.
- The pipeline (Task 7) passes `fetch_image=lambda u: fetch_bytes(u, max_bytes=10 * 1024 * 1024, ...)`.

- [ ] **Step 1: Write the failing tests** (`fetch_image` is a `Mock`):
  - `test_text_excludes_nav_and_script` — page text contains the article sentence but not the nav label or the script body
  - `test_table_with_colspan_and_blank_cells` — expanded width is correct; a blank cell becomes `None`
  - `test_images_resolve_relative_and_decode_data_uri` (Review Focus 2) — base `https://dhs.state.mn.us/reports/page.html`; `fetch_image` is called with `https://dhs.state.mn.us/img/chart.png` for `../img/chart.png` and with `https:` + the `//cdn...` path, but **not** for the `data:` image; 3 images total; `alt` and `caption` are stored
  - `test_image_fetch_failure_is_warning_not_error` — `fetch_image` raises `FetchError` → warning `image_fetch_failed` and the result is still returned
  - `test_candidate_links_pdf_xlsx_xls_and_download_text` — the expected 4 absolute URLs, de-duplicated
  - `test_js_only_page_warns` — `js_only_html()` → warning `js_rendered`
- [ ] **Step 2: Run** `manage.py test intake.tests.test_normalize_html` → FAIL
- [ ] **Step 3: Implement** `normalize_html`
- [ ] **Step 4: Run** → PASS
- [ ] **Step 5: Commit** `feat(intake): add HTML normalizer`

---

### Task 7: Persistence, document pipeline, and worker

**Files:**
- Create: `intake/services/persist.py`, `intake/services/pipeline.py`, `intake/services/worker.py`, `intake/management/__init__.py`, `intake/management/commands/__init__.py`, `intake/management/commands/run_intake_worker.py`, `intake/tests/test_pipeline.py`, `intake/tests/test_worker.py`

**Interfaces:**
- Consumes Tasks 1–6
- `persist_result(document: SourceDocument, result: NormalizedResult) -> None` — deletes the document's existing pages, then creates `DocumentPage`/`ExtractedTable`/`ExtractedImage`/`CandidateLink` rows, saving `preview_png` and image bytes through `ContentFile`. `CandidateLink` uses `get_or_create` on (document, url). Warnings are appended to `document.warnings` as dicts.
- `process_document(document: SourceDocument, *, throttle: HostThrottle) -> None`. Order follows spec §4 steps 2–6. If `stored_file` is empty, fetch `source_url` (set FETCHING, `throttle.wait`, `fetch_to_file` into a temp file, then save it to `stored_file` with an extension based on the detected kind and record `final_url`, `http_status`, `content_type`, `size_bytes`, `fetched_at`). Then compute `sha256`, run `detect_kind`, set `kind` or REJECTED with `VALID_TYPES_MESSAGE`, add `content_type_mismatch` and `duplicate` warnings (set `duplicate_of` to the earliest other document with the same `sha256`), set NORMALIZING, dispatch to the matching normalizer, and run `persist_result` and the READY status save inside a single `transaction.atomic()`. `NormalizeError` or non-retryable `FetchError` → FAILED with `error=str(exc)`. A retryable `FetchError` is **re-raised** for the worker. Every terminal outcome writes an `IntakeEvent`.
- `worker.py`:
  - `claim_next_job(now: datetime | None = None) -> ProcessingJob | None` — an eligible job is PENDING with `run_after <= now`, or RUNNING with `locked_at < now - 10 min`. Claim it with a conditional `UPDATE ... WHERE pk=? AND state=<seen state> AND (locked_at IS NULL OR locked_at=<seen>)` that sets RUNNING, `locked_at=now`, and `attempts += 1`; claimed only if 1 row was updated (this works on SQLite and Postgres)
  - `run_job(job: ProcessingJob, throttle: HostThrottle) -> None` — calls `process_document`. On success → DONE. On a retryable `FetchError` → if `attempts < 3`, PENDING with `run_after = now + (30 s if attempts == 1 else 120 s)`; otherwise GAVE_UP and the document becomes FAILED. On any other unexpected exception → GAVE_UP and FAILED with `error` set. If the document no longer exists (`SourceDocument.DoesNotExist` or the job row is gone), return quietly.
  - `run_once(throttle: HostThrottle) -> bool` — claims one job and runs it; returns whether a job ran
- Command `run_intake_worker [--once]` loops `run_once`, sleeping 1 s when it returns False. `--once` drains the queue and exits.

- [ ] **Step 1: Write the failing tests** in `test_pipeline.py` (temp `MEDIA_ROOT` as in `imports/tests/test_workflow.py`; patch `intake.services.pipeline.fetch_to_file` to write factory bytes):
  - `test_uploaded_pdf_becomes_ready_with_pages` — status READY, `sha256` has 64 characters, pages ≥ 1, preview file exists on disk
  - `test_url_html_records_fetch_metadata_and_links` — `final_url`, `http_status == 200`, `fetched_at` set, `kind == "html"`, ≥3 `CandidateLink`s
  - `test_png_is_rejected_with_message` — status REJECTED, `error == VALID_TYPES_MESSAGE`, no pages
  - `test_duplicate_sha_sets_duplicate_of_and_warns` — the second identical upload has `duplicate_of` = the first and a `duplicate` warning, and is still READY
  - `test_normalizer_failure_rolls_back` — patch `persist_result` so it creates one page and then raises `NormalizeError` → status FAILED and `DocumentPage.objects.filter(document=doc).count() == 0`
  - `test_retryable_fetch_error_propagates` — `fetch_to_file` raises `FetchError(retryable=True)` → `process_document` raises
- [ ] **Step 2: Write the failing tests** in `test_worker.py`:
  - `test_claims_pending_job_and_marks_done` — `run_once` → True; job DONE; `attempts == 1`
  - `test_skips_job_with_future_run_after`
  - `test_retry_backoff_then_gives_up` — `process_document` always raises a retryable error: after the 1st run, PENDING with `run_after ≈ now + 30 s`; after the 2nd, `≈ now + 120 s`; after the 3rd, GAVE_UP and the document FAILED
  - `test_reclaims_stale_running_job` — RUNNING with `locked_at = now - 11 min` → claimed; `now - 5 min` → not claimed
  - `test_document_deleted_mid_job_does_not_crash` (Review Focus 4) — claim the job, delete its document, then `run_job` → no exception and `SourceDocument.objects.count() == 0`
  - `test_command_once_drains_queue` — two pending jobs; `call_command("run_intake_worker", "--once")` → both DONE
- [ ] **Step 3: Run** `manage.py test intake.tests.test_pipeline intake.tests.test_worker` → FAIL
- [ ] **Step 4: Implement** `persist.py`, `pipeline.py`, `worker.py`, and the command
- [ ] **Step 5: Run** → PASS; then run all of `manage.py test intake imports` → PASS
- [ ] **Step 6: Commit** `feat(intake): add processing pipeline, job queue worker, and command`

---

### Task 8: Batch creation form, intake home, batch detail, retry/remove

**Files:**
- Create: `intake/forms.py`, `intake/services/batches.py`, `intake/views.py`, `intake/urls.py`, `intake/templates/intake/home.html`, `intake/templates/intake/batch_new.html`, `intake/templates/intake/batch_detail.html`, `intake/static/intake/poll.js`, `intake/tests/test_views_batches.py`
- Modify: `config/urls.py` (add `path("intake/", include("intake.urls"))` **before** the `""` include), `imports/templates/imports/base.html` (add `<a href="{% url 'intake:home' %}">Intake</a>` to the nav)

**Interfaces:**
- Consumes the Task 1 models and the Task 2 `VALID_TYPES_MESSAGE`
- `batches.py`:
  - `parse_urls(text: str) -> list[str]` — splits lines, strips them, drops blanks, de-duplicates while keeping order, and raises `ValidationError` naming any line that is not `http(s)://` with a host
  - `create_batch(*, title, default_scope, notes, files: list[UploadedFile], urls: list[str], actor) -> IntakeBatch` — inside one transaction, creates documents (scope = default_scope; uploads: save the row, then attach the file; URLs: `source_url` only) plus one PENDING `ProcessingJob` each, and writes the `batch_created` and `document_added` events
  - `retry_document(document, actor) -> None` — only for FAILED: resets the job (PENDING, `attempts=0`, `run_after=now`), sets the document to QUEUED, clears `error`, writes the `retried` event
  - `remove_document(document, actor) -> None` — allowed when the status is not READY; deletes the document (the job cascades) and writes the `removed` event on the batch
- `IntakeBatchForm(forms.Form)`: `title`, `default_scope`, `notes`, `urls` (Textarea), and `files`. Multi-file uploads use a `MultipleFileInput(ClearableFileInput)` with `allow_multiple_selected = True` and a matching `MultipleFileField` (the standard Django 5 pattern), with `accept=".pdf,.xlsx"`. `clean()`: at least one file or URL; total items ≤ `INTAKE_MAX_ITEMS_PER_BATCH`; each file ≤ `INTAKE_MAX_BYTES`; any file whose extension is not `.pdf`/`.xlsx` → error `f"{name}: {VALID_TYPES_MESSAGE}"`
- URL names (`app_name = "intake"`): `home` (`""`), `batch_new` (`"new/"`), `batch_detail` (`"<int:pk>/"`), `batch_status` (`"<int:pk>/status.json"`), `document_retry` (`"doc/<int:pk>/retry/"`, POST), `document_remove` (`"doc/<int:pk>/remove/"`, POST). The batch detail rows don't link to documents yet; Task 9 adds `document_detail` and that link.
- Views use `@login_required`, plus `@permission_required("intake.add_intakebatch", raise_exception=True)` on POST/mutation views and `intake.view_intakebatch` on read views.
- `batch_status` JSON: `{"status": batch.status, "documents": [{"id", "status", "pages", "tables", "images", "warnings": int}]}`. `poll.js` fetches it every 3000 ms while `status == "processing"`, then reloads the page once processing ends.

- [ ] **Step 1: Write the failing tests** (superuser login unless stated otherwise):
  - `test_parse_urls_strips_blanks_and_dedupes` (Review Focus 1) — `"\n https://a.gov/x.pdf \n\nhttps://a.gov/x.pdf\nhttps://b.gov/\n"` → `["https://a.gov/x.pdf", "https://b.gov/"]`
  - `test_parse_urls_rejects_bad_line` — `"ftp://x"` → `ValidationError`
  - `test_create_batch_with_files_and_urls_queues_jobs` — POST 1 PDF, 1 XLSX, and 2 URLs → redirect to batch detail; 4 documents QUEUED; 4 PENDING jobs; upload `stored_file` paths match `intake/<batch>/<doc>/original.pdf`; each document's `scope` equals `default_scope`
  - `test_upload_filename_with_spaces_and_unicode` — `"Rapport annuel – 2024 (final).pdf"` → `original_filename` is kept verbatim and the stored path is `.../original.pdf`
  - `test_png_upload_rejected_with_valid_types_message` — the form error contains `VALID_TYPES_MESSAGE`; no batch is created
  - `test_requires_file_or_url`
  - `test_too_many_items_rejected` — with `override_settings(INTAKE_MAX_ITEMS_PER_BATCH=2)` and 3 URLs → form error
  - `test_status_json_reports_processing` — documents QUEUED → `"processing"`
  - `test_retry_failed_document_requeues` — FAILED doc → after POST: QUEUED, job PENDING, `attempts == 0`
  - `test_remove_document_not_allowed_when_ready` — READY doc → 400 and the doc still exists
  - `test_user_without_permission_gets_403_on_create` — a regular user with no perms → POST new → 403
- [ ] **Step 2: Run** `manage.py test intake.tests.test_views_batches` → FAIL
- [ ] **Step 3: Implement** the forms, services, views, URLs, templates (reuse the classes from `imports/static/imports/app.css`; add no new CSS file unless needed), `poll.js`, the URL include, and the nav link
- [ ] **Step 4: Run** → PASS; then `manage.py test intake imports` → PASS
- [ ] **Step 5: Commit** `feat(intake): add batch creation, listing, status polling, retry and remove`

---

### Task 9: Document detail, metadata edit, candidate-link queuing

**Files:**
- Create: `intake/templates/intake/document_detail.html`, `intake/tests/test_views_documents.py`
- Modify: `intake/forms.py`, `intake/services/batches.py`, `intake/views.py`, `intake/urls.py`, `intake/templates/intake/batch_detail.html` (link each row to its document detail page)

**Interfaces:**
- `DocumentMetaForm(ModelForm)` on `SourceDocument` with fields `["scope", "year_hint"]`; `year_hint` is limited to 1990–2100
- `queue_links(document: SourceDocument, link_ids: list[int], actor) -> list[SourceDocument]` — inside a transaction, re-reads the links with `select_for_update()`, filtered to `document=document` and `queued_as__isnull=True`. For each one, it creates a child `SourceDocument` (`origin` FROM_WORKBOOK if the parent kind is xlsx, otherwise FROM_PAGE; `parent=document`; same batch; `scope` = the parent's scope; `source_url` = the link URL), a PENDING job, and sets `link.queued_as`. Writes the `links_queued` event. Returns the new documents.
- URLs: `document_detail` (`"doc/<int:pk>/"`, GET shows the page, POST saves `DocumentMetaForm`), `document_queue_links` (`"doc/<int:pk>/queue-links/"`, POST with a `link_ids` multi-value field), `document_original` (`"doc/<int:pk>/original/"`, a `FileResponse` with `as_attachment=True` and `filename=original_filename or basename`)
- The template shows metadata, warnings, a duplicate link, the meta form, a links checklist (already-queued links are disabled and link to their child), and pages showing the preview `<img>`, text inside `<details>`, tables as `<table>` (render `None` as an empty cell), and image thumbnails. Decorative images are wrapped in `<details>` labeled "Show N small/decorative images".

- [ ] **Step 1: Write the failing tests**:
  - `test_detail_renders_pages_tables_images` — a READY document with persisted factory results → 200; contains the page text, a `<table>`, and an `<img` pointing at the preview URL; decorative images are inside `<details>`
  - `test_edit_scope_and_year_hint` — POST `scope="National"` and `year_hint=2023` → saved; `year_hint=1800` → form error
  - `test_queue_selected_links_creates_children` — 2 of 3 links → 2 children with the correct `origin`/`parent`/`source_url` and PENDING jobs; `queued_as` is set
  - `test_queue_same_link_twice_creates_one_child` (Review Focus 5) — POST the same `link_ids` twice → only 1 child document
  - `test_cannot_queue_links_from_other_document` — `link_ids` belonging to another document → ignored, 0 children
  - `test_download_original_uses_original_filename` — the `Content-Disposition` header contains the original filename
- [ ] **Step 2: Run** `manage.py test intake.tests.test_views_documents` → FAIL
- [ ] **Step 3: Implement**
- [ ] **Step 4: Run** → PASS; then `manage.py test intake imports` → PASS
- [ ] **Step 5: Commit** `feat(intake): add document detail, metadata edit, and link queuing`

---

### Task 10: Docker worker service, README, and end-to-end check

**Files:**
- Modify: `compose.yml`, `README.md`, `.gitignore` at the repo root (create it if missing: `__pycache__/`, `*.pyc`, `kara_eim_portal/db.sqlite3`, `kara_eim_portal/media/`, `.vs/`, `.venv/`)

**Interfaces:**
- The `compose.yml` service `worker` uses the same `build`, `environment`, `volumes`, and `depends_on` as `web`, with `command: python manage.py run_intake_worker`
- A README section, "Intake worker", gives the second-terminal command for macOS/Linux and Windows, and says that only one worker should run on SQLite

- [ ] **Step 1: Edit** `compose.yml`, the README, and `.gitignore`
- [ ] **Step 2: Verify** with `docker compose config` → exits 0 and lists `db`, `web`, and `worker` (if Docker is not installed, record that this was skipped)
- [ ] **Step 3: Manual end-to-end check** (no network needed): `manage.py migrate`, `runserver`, and in a second terminal `run_intake_worker`. Upload a reportlab text PDF, the repo's `EIM Field Inventory & Data Tracking (1).xlsx`, and a `.png`. Expected: PDF READY with a preview; XLSX READY with candidate links from `Report Asset URL` and an `eim_workbook` warning; PNG rejected by the form with the valid-types message. Record the results in the commit message body.
- [ ] **Step 4: Run** the full suite `manage.py test` → all PASS
- [ ] **Step 5: Commit** `chore(intake): add worker service, docs, and gitignore`
