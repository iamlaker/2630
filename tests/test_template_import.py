import hashlib
import tempfile
import unittest
from pathlib import Path

from forecast_engine import InMemoryWorkbookEngine
from template_catalog import TemplateImportService


class TemplateImportTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "template.xlsx"
        self.source.write_bytes(b"unchanged workbook")
        self.original_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.service = TemplateImportService(
            self.root / "store", self.root / "catalog.sqlite3", InMemoryWorkbookEngine()
        )

    def tearDown(self):
        self.service.close()
        self.temp_dir.cleanup()

    def test_import_creates_fingerprint_version_catalog_and_audit(self):
        result = self.service.import_template(self.source)

        self.assertEqual(result["import_status"], "success")
        self.assertEqual(result["template_fingerprint"], self.original_hash)
        self.assertEqual(result["template_version"], 1)
        self.assertEqual(result["year_mapping"], {str(year): column for year, column in zip(range(2026, 2031), "DEFGH")})
        self.assertEqual(result["worksheet"]["index"], 2)
        indicators = {item["display_name"]: item for item in result["indicator_catalog"]}
        self.assertEqual(indicators["并表口径总资产"]["classification"], "input")
        self.assertEqual(indicators["并表口径总资产"]["group"], "规模假设")
        self.assertEqual(indicators["对公贷款利率"]["group"], "价格假设")
        self.assertEqual(indicators["财富管理AUM规模增速"]["group"], "中收假设")
        self.assertEqual(indicators["归母净利润"]["classification"], "output")
        self.assertEqual(indicators["归母净利润"]["cell_address"], "B6")
        self.assertEqual(indicators["归母净利润"]["year_values"]["2026"], 757.8)
        self.assertEqual(indicators["归母净利润"]["unit"], "亿元")
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.original_hash)
        self.assertEqual(self.service.list_audit_events()[-1]["operation"], "catalog_generated")

    def test_same_fingerprint_reuses_version(self):
        first = self.service.import_template(self.source)
        second = self.service.import_template(self.source)

        self.assertEqual(first["template_version_id"], second["template_version_id"])
        self.assertEqual(len(self.service.list_template_versions()), 1)

    def test_catalog_can_be_queried_by_stable_version_id(self):
        imported = self.service.import_template(self.source)
        catalog = self.service.get_indicator_catalog(imported["template_version_id"])

        self.assertEqual(catalog["template_fingerprint"], self.original_hash)
        self.assertEqual(catalog["catalog_status"], "generated")

    def test_rejects_non_xlsx_with_clear_reason(self):
        invalid = self.root / "template.xls"
        invalid.write_bytes(b"old excel")
        result = self.service.import_template(invalid)
        self.assertEqual(result["import_status"], "failed")
        self.assertIn(".xlsx", result["error"])

    def test_explicit_input_override_marks_indicator_without_source_rules(self):
        result = self.service.import_template(
            self.source, input_overrides={"归母净利润": "重要参数"}
        )
        indicator = next(item for item in result["indicator_catalog"] if item["display_name"] == "归母净利润")
        self.assertEqual(indicator["classification"], "input")
        self.assertEqual(indicator["group"], "重要参数")
        self.assertNotIn("source_cell", indicator)

    def test_missing_summary_sheet_fails_with_audit_reason(self):
        engine = InMemoryWorkbookEngine(summary_sheet_missing=True)
        service = TemplateImportService(self.root / "bad-store", self.root / "bad.sqlite3", engine)
        try:
            result = service.import_template(self.source)
            self.assertEqual(result["import_status"], "failed")
            self.assertIn("汇总展示表", result["error"])
            event = service.list_audit_events()[-1]
            self.assertEqual(event["operation"], "template_import_failed")
            self.assertEqual(event["status"], "failed")
        finally:
            service.close()

    def test_real_excel_template_catalog(self):
        from forecast_engine import ExcelComWorkbookEngine, TEMPLATE_PATH

        if not TEMPLATE_PATH.exists():
            self.skipTest("真实模板不存在")
        service = TemplateImportService(
            self.root / "com-store", self.root / "com.sqlite3", ExcelComWorkbookEngine()
        )
        try:
            result = service.import_template(TEMPLATE_PATH)
            self.assertEqual(result["import_status"], "success", result["error"])
            self.assertEqual(result["worksheet"], {"name": "汇总展示表", "index": 2})
            self.assertGreater(len(result["indicator_catalog"]), 100)
            self.assertEqual(result["year_mapping"], {str(year): column for year, column in zip(range(2026, 2031), "DEFGH")})
            self.assertTrue(any(item["group"] == "重要参数" and item["classification"] == "input" for item in result["indicator_catalog"]))
            self.assertTrue(any(item["group"] == "规模假设" and item["classification"] == "input" for item in result["indicator_catalog"]))
            self.assertTrue(any(item["group"] == "价格假设" and item["classification"] == "input" for item in result["indicator_catalog"]))
            self.assertTrue(any(item["group"] == "中收假设" and item["classification"] == "input" for item in result["indicator_catalog"]))
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
