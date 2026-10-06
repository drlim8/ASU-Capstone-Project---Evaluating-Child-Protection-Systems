# KARA EIM Data Portal

This Django prototype lets authorized KARA users upload an EIM Excel workbook, run repeatable validation, preview staged records, review existing EIM category groupings, approve an import, and preserve a versioned publication history.

## Included workflow

1. Upload an `.xlsx` workbook.
2. Preserve the original file and calculate its SHA-256 fingerprint.
3. Detect supported EIM sheets and read tabular rows.
4. Validate required sheets, columns, identifiers, pick-list values, duplicate keys, and cross-sheet references.
5. Keep missing values visible. The portal does not replace blanks with zero.
6. Stage records and show errors, warnings, sheet counts, and category-based groupings.
7. Require a human reviewer to approve or reject the import.
8. Copy approved staged rows into versioned published records inside one database transaction.
9. Record upload, validation, approval, rejection, and failure events in an audit log.

## Supported EIM sheets

The first prototype processes these required sheets:

- Master Data Sources
- Source Assets
- Master Observation Data
- Metric Comparability
- Rubric Variable Map

It also processes these optional sheets when present:

- Minnesota Policy Tracking
- National Policy Tracking
- Observation Data Schema
- Policy Metrics Schema

`Validation Lists` supplies approved pick-list values. The calculator and reference-only sheets remain in the original workbook but are not loaded as data records.

## Run in VS Code

### macOS or Linux

Open this folder in VS Code, then open **Terminal > New Terminal** and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open <http://127.0.0.1:8000/> and sign in with the superuser account.

### Windows PowerShell

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open <http://127.0.0.1:8000/>.

The included `.vscode/launch.json` also provides a **Run KARA data portal** debug configuration after the environment is installed.

## Create a safe demo workbook

Do not commit the real EIM workbook to GitHub. Generate a small synthetic workbook for demonstrations:

```bash
python scripts/create_demo_workbook.py
```

Upload `data/raw/demo_eim_workbook.xlsx` through the portal.

## Run automated tests

```bash
python manage.py test
```

The tests cover valid workbook processing, missing required sheets, broken cross-sheet references, uploading, validation, approval, and publishing.

## Use PostgreSQL with Docker

SQLite is the default for local development. For the longer-term PostgreSQL configuration:

```bash
docker compose up --build
```

In another terminal, create the first reviewer account:

```bash
docker compose exec web python manage.py createsuperuser
```

Then open <http://127.0.0.1:8000/>.

The passwords in `compose.yml` are only local defaults. Replace them before deploying the portal.

## Roles and permissions

- Any authenticated account can upload and inspect imports.
- Accounts with the Django `change dataset import` permission can approve or reject an import.
- A superuser automatically has all review permissions.
- The Django administration site is available at `/admin/`.

## Validation behavior

Blocking errors include:

- A required EIM sheet is missing.
- A required column is missing.
- A record key is missing.
- A duplicate record key exists within a sheet.
- An identifier has an invalid format.
- A value is outside the workbook's approved validation list.
- A required cross-sheet identifier does not exist.

Warnings include:

- Recommended metadata is blank.
- An observation does not include a source URL.
- A listed source asset cannot be matched during comparability review.

Warnings remain visible but do not automatically block human review. Errors must be fixed before approval.

## Project layout

```text
config/                    Django settings and top-level URLs
imports/models.py          Imports, staged records, published records, audit events
imports/services/etl.py    Workbook extraction, transformation, and validation
imports/services/schema.py Supported sheets and field rules
imports/templates/         Accessible upload and review screens
imports/tests/             ETL and approval workflow tests
data/raw/                  Local-only input files, ignored by Git
data/processed/            Local-only outputs, ignored by Git
```

## Production work still required

This is a working MVP, not a finished KARA production deployment. Before production use:

- Confirm every validation rule with the KARA methodology and Platform teams.
- Replace the development secret key and disable debug mode.
- Use HTTPS and an approved identity provider.
- Store original files in approved encrypted object storage.
- Add malware scanning and file-retention rules.
- Add database backups, monitoring, and restore testing.
- Add background job processing if workbooks become large.
- Confirm privacy and access requirements before loading sensitive information.
- Decide whether category groupings or statistical machine-learning clusters are required.
