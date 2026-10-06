import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from imports.services.etl import inspect_workbook

from .workbook_factory import build_test_workbook


class WorkbookInspectionTests(SimpleTestCase):
    def inspect_bytes(self, content):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "test.xlsx"
            path.write_bytes(content)
            return inspect_workbook(path)

    def test_valid_workbook_is_ready_for_review(self):
        result = self.inspect_bytes(build_test_workbook())
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["records"]), 6)
        self.assertEqual(result["clusters"]["Scope"]["Minnesota"], 2)

    def test_missing_core_sheet_is_blocking_error(self):
        result = self.inspect_bytes(build_test_workbook(include_all_core=False))
        codes = [item["code"] for item in result["errors"]]
        self.assertIn("missing_sheet", codes)

    def test_unknown_source_is_blocking_error(self):
        result = self.inspect_bytes(build_test_workbook(invalid_source=True))
        codes = [item["code"] for item in result["errors"]]
        self.assertIn("unknown_source", codes)
