# Batch Intake & Normalization — Design

**Date:** 2026-10-09
**Status:** Approved in brainstorming, pending written-spec review
**Sub-project:** 1 of 4 (intake → extraction → side-by-side review → CSV export)

## 1. Purpose

KARA staff need to submit batches of state child-protection sources — PDFs, Excel workbooks, and web page URLs — and have each one converted into a single **normalized document bundle**: the preserved original plus page-by-page text, tables, and images. Later sub-projects (value extraction, human review, CSV export in the `Master Observation Data` format) read only these bundles and never deal with source formats directly.

### Success criteria

- A user can submit a mix of `.pdf` files, `.xlsx` files, and URLs in one batch.
- Every accepted source ends up with its original bytes preserved, a SHA-256 fingerprint, and normalized pages, tables, and images.
- Embedded figures (charts, graphs, infographics) are saved as image files, with their page and position recorded.
- Files that are only images (`.png`, `.jpg`, etc.) and other unsupported types are rejected with the message: *"Valid file types are Excel (.xlsx), PDF, or a URL linking to one of these."*
- URLs found in workbooks and links to PDF or XLSX files on web pages are shown as candidate links. The user chooses which ones to queue.
- Missing values are never turned into zero.
- The existing EIM workbook import (`imports` app) continues to work unchanged.

### Out of scope

OCR; extracting label/value/date; the side-by-side review UI; CSV export; Claude API usage and budget guardrails; rendering pages that depend on JavaScript (headless browser). Each of these belongs to a later sub-project.

## 2. Decisions made during brainstorming

| Topic | Decision |
|---|---|
| Images | Extract and save embedded figures. Reject sources that are entirely an image file. Accept scanned PDFs (no text layer) **with a warning**, not a rejection. |
| `.xlsx` role | Keep every sheet as data content **and** detect URL columns, offering them as candidate links. |
| HTML URLs | Accepted. Save a timestamped raw snapshot and extract text, tables, and images. PDF and XLSX links become candidate links. |
| Architecture | New `intake` Django app inside `kara_eim_portal` (option A). `imports` is not modified. |
| Background work | Queue backed by the database plus a `run_intake_worker` management command. No Redis or Celery. |
| Deployment | Local and Docker (`compose.yml`) for now. |
| Expected batch size | Up to about 50 documents. Larger batches still work, only more slowly. |
| PDF libraries | `pdfplumber` (text/tables) and `pypdfium2` (previews/images). PyMuPDF is avoided because of its AGPL license. |

## 3. Data model (`intake` app)

### `IntakeBatch`
- `title` (char 200), `default_scope` (char 100, blank OK), `notes` (text, blank OK)
- `created_by` (FK user, PROTECT), `created_at`
- `status` — **derived** property (not stored): `processing` if any document is `queued`/`fetching`/`normalizing`; otherwise `has_problems` if any document is `rejected`/`failed`; otherwise `ready`.

### `SourceDocument`
- `batch` (FK IntakeBatch, CASCADE), `parent` (FK self, null, SET_NULL)
- `kind`: `pdf` | `xlsx` | `html` | `unknown` (`unknown` only until detection finishes or on rejection)
- `origin`: `upload` | `url` | `from_workbook` | `from_page`
- `source_url` (URL, blank), `final_url` (URL, blank), `http_status` (int, null)
- `original_filename` (char 255, blank), `stored_file` (FileField, blank) — the original bytes, or the raw HTML snapshot
- `sha256` (char 64, indexed, blank until stored), `content_type` (char 100, blank), `size_bytes` (int, null), `fetched_at` (datetime, null)
- `scope` (char 100, blank; initialized from `batch.default_scope`), `year_hint` (int, null)
- `status`: `queued` | `fetching` | `normalizing` | `ready` | `rejected` | `failed`
- `warnings` (JSON list of `{code, message, page?}`), `error` (text, blank)
- `duplicate_of` (FK self, null, SET_NULL) — set when `sha256` matches an earlier document. The new document is still processed and kept.
- `created_at`, `updated_at`

### `CandidateLink`
- `document` (FK SourceDocument, CASCADE), `url`, `label` (link text or the workbook cell location, e.g. `Source Assets!H12`), `context` (char 255, blank — e.g. the column header)
- `queued_as` (FK SourceDocument, null, SET_NULL) — set once the user queues it
- Unique per (`document`, `url`).

### `DocumentPage`
- `document` (FK, CASCADE), `number` (1-based), `label` (e.g. the sheet name for XLSX; blank otherwise)
- `text` (text), `has_text_layer` (bool), `preview_image` (ImageField, blank; PDFs only)
- Unique per (`document`, `number`).

### `ExtractedTable`
- `page` (FK DocumentPage, CASCADE), `index`
- `rows` (JSON: list of lists of strings or `null`; `null` = blank cell, never `0`)
- `bbox` (JSON `[x0, y0, x1, y1]` or null; PDFs only), `header_guess` (JSON list or null)

### `ExtractedImage`
- `page` (FK DocumentPage, CASCADE), `index`, `file` (ImageField), `sha256`
- `width`, `height` (px), `bbox` (JSON or null; PDFs), `src`, `alt`, `caption` (HTML; blank otherwise)
- `decorative` (bool) — true when either dimension is below 64 px or the area is below 10,000 px²

### `ProcessingJob`
- `document` (OneToOne SourceDocument, CASCADE)
- `state`: `pending` | `running` | `done` | `gave_up`
- `attempts` (int), `run_after` (datetime), `locked_at` (datetime, null), `last_error` (text)

### `IntakeEvent`
- `batch` (FK, CASCADE), `document` (FK, null, CASCADE), `actor` (FK user, null, PROTECT), `action` (char 80), `details` (JSON), `created_at`
- Actions include: `batch_created`, `document_added`, `fetched`, `rejected`, `failed`, `normalized`, `links_queued`, `retried`, `removed`.

File storage paths (under `MEDIA_ROOT`): `intake/<batch_id>/<document_id>/original.<ext>`, `.../pages/<n>.png`, `.../images/<page>-<index>.<ext>`.

## 4. Processing flow

1. **Submit.** The user creates a batch with files and/or URLs (one per line). Each file or URL becomes a `SourceDocument(status=queued)` plus a `ProcessingJob(pending)`. Uploaded files are stored and hashed at this point. Obviously unsupported extensions (images, `.docx`, `.csv`, …) are rejected by the form.
2. **Fetch** (URL sources only) → `status=fetching`. See §5.
3. **Detect type** from the bytes (§5.3). Anything that is not PDF, XLSX, or HTML → `status=rejected` with the valid-types message.
4. **Duplicate check** on `sha256` → set `duplicate_of` and add a `duplicate` warning.
5. **Normalize** → `status=normalizing`. The matching normalizer (§6) runs. All of its rows are written in **one database transaction**. If it fails, the transaction rolls back and the document is set to `failed`, with no partial pages.
6. **Done** → `status=ready`, and the job becomes `done`.
7. **Candidate links** are stored but not fetched. The user selects links and clicks **Queue selected**. Each one becomes a child `SourceDocument(origin=from_workbook|from_page, parent=…)` in the same batch and starts at step 2.

## 5. Fetching and type detection

### 5.1 URL fetcher
- Schemes: `http` and `https` only.
- **SSRF guard:** resolve the host before connecting and after every redirect. Refuse loopback, private, link-local, reserved, and multicast addresses.
- Timeout 30 s (connect + read), at most 5 redirects, streamed download aborted after **50 MB**.
- User-Agent: `KARA-EIM-Intake/1.0 (+contact in settings)`.
- **Politeness:** at least 2 s between requests to the same host, enforced by the worker.
- Records `final_url`, `http_status`, `content_type`, `fetched_at`.
- HTTP 4xx → `failed`, no retry. Network errors and 5xx → retry (§7).

### 5.2 Upload limits
- At most 50 MB per uploaded file and at most 100 items per batch submission.

### 5.3 Type detection (from the bytes; extension and Content-Type are hints only)
- PDF: starts with `%PDF-`.
- XLSX: a ZIP archive containing `[Content_Types].xml` and an `xl/` directory.
- HTML: the first 2 KB (after skipping whitespace and a BOM) contains `<!doctype html` or `<html`, or Content-Type is `text/html`.
- Image signatures (PNG, JPEG, GIF, WebP, TIFF, BMP) → rejected with the valid-types message.
- Anything else → rejected with the valid-types message.
- If the Content-Type header disagrees with the detected type, add a `content_type_mismatch` warning and continue.

## 6. Normalizers

All normalizers implement `normalize(document, file_path) -> NormalizedResult`. They are pure Python with no Django imports. The result is a plain dataclass (pages, tables, images, candidate links, warnings) that a separate persistence function writes to the models. This makes the normalizers testable without a database.

### 6.1 PDF (`pdfplumber` + `pypdfium2`)
- For each page: text (`extract_text`), tables (`extract_tables` with bboxes), embedded images (saved with their bbox), and a 150 DPI preview PNG.
- `has_text_layer = False` when the page text is shorter than 20 non-whitespace characters. If every page lacks a text layer, add a document warning `no_text_layer` ("No text layer found — this may be a scanned document and need OCR.").
- An encrypted PDF that needs a password, or a corrupt PDF → `failed` with a readable error.

### 6.2 XLSX (`openpyxl`)
- Each worksheet is one page (`label` = sheet name). Its used range becomes one table. Cell values are stored as strings; blanks become `null`. Page text is the cells joined row by row, for searching.
- Embedded images are extracted when openpyxl exposes them (`ws._images`). If none can be read, nothing is lost: the original file is preserved.
- **URL columns:** a column whose header contains `url` or `link` (case-insensitive), or where at least 50% of non-blank cells are `http(s)` URLs. Each distinct URL becomes a `CandidateLink` with `label` = `Sheet!Cell` and `context` = the header.
- **EIM detection:** if the workbook contains the required EIM sheets (from `imports.services.schema`), add an informational warning `eim_workbook` that links to the existing EIM import. The two flows are not merged.

### 6.3 HTML (`requests` + `BeautifulSoup`)
- The raw HTML snapshot is stored as `stored_file`.
- Readable text: `<script>`, `<style>`, `<nav>`, `<header>`, `<footer>`, `<noscript>` are removed, then the remaining text is collapsed. Stored as page 1.
- Every `<table>` becomes an `ExtractedTable` (colspan/rowspan expanded, blanks become `null`).
- `<img>` (and `<figure>` with `<figcaption>`): images are downloaded with the same fetcher rules (SSRF guard, 10 MB per image, at most 100 images per page) and stored with `src`, `alt`, and `caption`. Failed image downloads become warnings, not document failures.
- Candidate links: `<a href>` values whose resolved URL path ends in `.pdf`, `.xlsx`, or `.xls`, or whose link text contains "download". `.xls` links are listed but will be rejected if queued (it is not a supported type).
- If the extracted text is under 200 characters and the page contains `<script>` tags, add a warning `js_rendered` ("This page may need a browser to render; consider uploading the PDF instead.").

## 7. Worker

- `python manage.py run_intake_worker [--once]` loops: claim one job with `select_for_update(skip_locked=True)` where `state=pending` and `run_after <= now`, or where `state=running` and `locked_at` is older than 10 minutes (crash recovery). It processes the job, then sleeps 1 s when the queue is empty. On SQLite, which has no `skip_locked`, it falls back to a conditional `UPDATE … WHERE state=pending` claim. Only one worker is supported on SQLite.
- **Retries:** at most 3 attempts, only for network errors and HTTP 5xx, with backoff of 30 s and then 120 s. After that the job is `gave_up` and the document is `failed`.
- **No retry** for rejected types, HTTP 4xx, or corrupt or encrypted files.
- A manual **Retry** in the UI resets `attempts` and requeues the job.

## 8. Screens

Server-rendered templates that extend `imports/base.html` (same CSS). Login is required for all of them.

1. **Intake home** `/intake/` — recent batches with derived status and counts. "New batch" button. A link is added to the portal navigation.
2. **New batch** `/intake/new/` — title, default scope, notes, multi-file input (`accept=".pdf,.xlsx"`), and a URL textarea. Validation: at least one file or URL; per-file size; extension denylist with the valid-types message; URLs must be well-formed `http(s)`.
3. **Batch detail** `/intake/<batch_id>/` — document table (name/URL, kind, origin, status, page/table/image counts, warning badges). Polls a small JSON endpoint every 3 s while any document is processing. Retry button for `failed` documents; Remove button for documents that are not yet ready.
4. **Document detail** `/intake/doc/<doc_id>/` — metadata, warnings, duplicate link, an editable scope/year_hint form, a candidate-links checklist with **Queue selected**, and the content shown page by page: preview image (PDF), text in a collapsible block, tables as HTML tables, and image thumbnails (decorative images hidden behind a toggle). A **Download original** link is included.

**Permissions:** `intake.add_intakebatch` to create batches, queue links, retry, and remove. `intake.view_intakebatch` to view.

## 9. Configuration & running

- New settings with defaults: `INTAKE_MAX_BYTES = 50 MB`, `INTAKE_FETCH_TIMEOUT = 30`, `INTAKE_HOST_DELAY = 2.0`, `INTAKE_USER_AGENT`, `INTAKE_MAX_ITEMS_PER_BATCH = 100`.
- `requirements.txt` gains `pdfplumber`, `pypdfium2`, `requests`, `beautifulsoup4`, `Pillow` (pinned to version ranges in the same style as the existing ones).
- `compose.yml` gains a `worker` service (same image and environment as `web`, command `python manage.py run_intake_worker`, sharing the `uploaded_files` volume).
- The README gains a section on running the worker in a second terminal.

## 10. Testing

Tests go in `intake/tests/`, following the `imports/tests/` pattern, and are written before the implementation (TDD).

- **Fixture factory** that builds small files in code: a text PDF with a table, an image-only PDF, a PDF with an embedded image, an `.xlsx` with a URL column, an EIM-shaped `.xlsx` (reusing `imports/tests/workbook_factory.py`), an HTML page with a table, an image, and PDF links, a JS-only HTML page, and `.png`/`.docx` files that must be rejected.
- **Network is always mocked.** No test reaches the internet.
- Coverage:
  - type detection, including mismatch warnings
  - SSRF guard for direct and redirected private IPs
  - size and timeout aborts
  - duplicate detection
  - the shape of each normalizer's `NormalizedResult`
  - the `no_text_layer` and `js_rendered` warnings
  - blanks preserved as `null`
  - URL-column detection
  - queuing candidate links
  - retry and give-up rules
  - stale-lock recovery
  - transaction rollback when a normalizer fails
  - view permissions
  - form rejection messages

## 11. Interfaces for later sub-projects

- Extraction (sub-project 2) reads `SourceDocument(status=ready)` → `DocumentPage` / `ExtractedTable` / `ExtractedImage`, and writes its own models linked to the page and table it extracted from.
- Review (sub-project 3) uses `DocumentPage.preview_image`, the `stored_file` snapshot, and table and image bboxes to show the source next to extracted values.
- `SourceDocument` lines up with the EIM `Source Assets` sheet (URL, file format, publication year). A `Report Asset ID` will be assigned at publish/export time, not at intake.
