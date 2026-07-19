import tempfile
import unittest
from pathlib import Path

from engine_regression import EngineRegressionSample, compare_engine_results, execute_regression_sample, unavailable_candidate_validation
from forecast_engine import CalculationRequest, ConfirmedInputRule, InMemoryWorkbookEngine
from ubuntu_engine import LibreOfficeCalcEngine, UbuntuEngineUnavailable


def regression_result(*, engine="excel_com", version="16.0", output=100.0, input_value=.0185, cycle=.01, fingerprint="fp"):
    return {
        "sample_id": "task14-10y-treasury-2026-plus-10bp", "publication_id": "publication",
        "template_fingerprint": fingerprint, "actual_template_fingerprint": fingerprint,
        "engine": {"name": engine, "version": version, "production_ready": engine == "excel_com"},
        "inputs": {"10年期国债收益率": {"2026": input_value}},
        "outputs": {"归母净利润": {"2026": output, "2027": 101.0}},
        "cycle": {"converged": True, "iterations": 2, "final_differences": {"profitability": cycle, "segment": 0}},
        "calculation_status": "valid", "stage_timings": {}, "diagnostics": {}, "error": None,
    }


class EngineRegressionTests(unittest.TestCase):
    def test_same_results_pass_but_candidate_remains_not_production_approved(self):
        reference = regression_result()
        candidate = regression_result(engine="libreoffice_calc", version="25.2")
        result = compare_engine_results(reference, candidate)
        self.assertEqual(result["validation_state"], "valid")
        self.assertEqual(result["differences"], [])
        self.assertFalse(result["production_ready"])

    def test_output_difference_names_indicator_year_engines_and_versions(self):
        result = compare_engine_results(
            regression_result(), regression_result(engine="libreoffice_calc", version="25.2", output=100.02),
            output_tolerance=.001,
        )
        self.assertEqual(result["validation_state"], "engine_difference")
        difference = result["differences"][0]
        self.assertEqual((difference["category"], difference["indicator"], difference["year"]), ("output", "归母净利润", "2026"))
        self.assertEqual((difference["reference_engine"], difference["reference_version"]), ("excel_com", "16.0"))
        self.assertEqual((difference["candidate_engine"], difference["candidate_version"]), ("libreoffice_calc", "25.2"))

    def test_input_cycle_and_template_fingerprint_are_compared(self):
        candidate = regression_result(engine="libreoffice_calc", input_value=.0186, cycle=.2, fingerprint="other")
        result = compare_engine_results(regression_result(), candidate, input_tolerance=0, cycle_tolerance=.1)
        self.assertEqual({item["category"] for item in result["differences"]}, {"input", "cycle", "template"})

    def test_actual_template_fingerprint_is_compared_to_sample(self):
        candidate = regression_result(engine="libreoffice_calc")
        candidate["actual_template_fingerprint"] = "wrong-file"
        result = compare_engine_results(regression_result(), candidate)
        self.assertTrue(any(item["indicator"] == "candidate_actual_template_fingerprint" for item in result["differences"]))

    def test_negative_tolerance_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-negative"):
            compare_engine_results(regression_result(), regression_result(), output_tolerance=-1)

    def test_shared_contract_execution_captures_input_output_cycle_and_engine(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "template.xlsx"; path.write_bytes(b"template")
        sample = EngineRegressionSample(
            "sample", "publication", "template-fingerprint", path,
            CalculationRequest(input_adjustment={2026: .0185}, input_rule=ConfirmedInputRule("10年期国债收益率", "参数", {2026: "A1"})),
        )
        result = execute_regression_sample(InMemoryWorkbookEngine(), sample)
        self.assertEqual(result["inputs"]["10年期国债收益率"]["2026"], .0185)
        self.assertIn("利润", result["outputs"])
        self.assertTrue(result["cycle"]["converged"])
        self.assertEqual(result["engine"]["name"], "in_memory")

    def test_unavailable_ubuntu_adapter_is_explicit_and_not_production_ready(self):
        engine = LibreOfficeCalcEngine(soffice_path="")
        engine.soffice_path = None
        sample = EngineRegressionSample("sample", "publication", "fp", Path("missing.xlsx"), CalculationRequest())
        validation = unavailable_candidate_validation(sample, engine)
        self.assertEqual(validation["validation_state"], "engine_difference")
        self.assertFalse(validation["production_ready"])
        self.assertEqual(validation["differences"][0]["absolute_difference"], "engine_unavailable")
        with self.assertRaises(UbuntuEngineUnavailable):
            engine.open_isolated(Path("missing.xlsx"))


if __name__ == "__main__":
    unittest.main()
