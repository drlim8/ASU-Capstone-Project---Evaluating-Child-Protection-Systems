from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from imports.tests.workbook_factory import build_test_workbook


output = PROJECT_ROOT / "data" / "raw" / "demo_eim_workbook.xlsx"
output.write_bytes(build_test_workbook())
print(f"Created {output}")
