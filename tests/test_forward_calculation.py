import unittest

from forecast_engine import (
    CalculationRequest,
    ConfirmedInputRule,
    InMemoryWorkbookEngine,
    run_forward_calculation,
)


class ForwardCalculationTests(unittest.TestCase):
    def test_valid_calculation_converges_and_returns_outputs(self):
        engine = InMemoryWorkbookEngine()
        result = run_forward_calculation(
            engine,
            CalculationRequest(
                input_adjustment={2027: 120},
                input_rule=ConfirmedInputRule("资产规模", "参数表", {2027: "D10"}),
            ),
        )
        self.assertEqual(result["calculation_status"], "valid")
        self.assertTrue(result["cycle_converged"])
        self.assertEqual(result["input_adjustments"]["values"]["2027"], 120)
        self.assertLessEqual(result["final_difference"], 0.1)
        self.assertIn("利润", result["output_indicators"])
        self.assertIn("stage_timings", result)

    def test_baseline_read_can_be_skipped(self):
        engine = InMemoryWorkbookEngine()
        result = run_forward_calculation(engine, CalculationRequest(), read_baseline=False)
        self.assertEqual(result["calculation_status"], "valid")
        self.assertEqual(result["summary_before"], {})
        self.assertIn("利润", result["output_indicators"])

    def test_cycle_not_converged(self):
        engine = InMemoryWorkbookEngine(differences=[1.0, 0.5])
        result = run_forward_calculation(engine, CalculationRequest(), max_iterations=2)
        self.assertEqual(result["calculation_status"], "cycle_not_converged")
        self.assertFalse(result["cycle_converged"])
        self.assertEqual(result["iterations"], 2)

    def test_five_year_adjustment_uses_confirmed_year_mapping(self):
        engine = InMemoryWorkbookEngine()
        values = {year: year * 10 for year in range(2026, 2031)}
        rule = ConfirmedInputRule(
            "资产规模", "参数表", {year: f"D{year - 2000}" for year in values}
        )
        result = run_forward_calculation(
            engine, CalculationRequest(input_adjustment=values, input_rule=rule)
        )
        self.assertEqual(result["calculation_status"], "valid")
        self.assertEqual(engine.adjustments, values)

    def test_real_business_rule_on_isolated_excel_copy(self):
        from forecast_engine import ExcelComWorkbookEngine, TEMPLATE_PATH

        rule = ConfirmedInputRule(
            "对公贷款利率",
            "信贷业务",
            {2026: "C19", 2027: "D19", 2028: "E19", 2029: "F19", 2030: "G19"},
            source_sheet_index=8,
        )
        result = run_forward_calculation(
            ExcelComWorkbookEngine(),
            CalculationRequest({year: 0.041 for year in range(2026, 2031)}, rule),
            max_iterations=5,
        )
        self.assertEqual(result["calculation_status"], "valid")
        self.assertEqual(result["iterations"], 1)
        self.assertEqual(len(result["output_indicators"]), 161)

    def test_calculation_failed(self):
        engine = InMemoryWorkbookEngine(fails=True)
        result = run_forward_calculation(engine, CalculationRequest())
        self.assertEqual(result["calculation_status"], "calculation_failed")
        self.assertIsNotNone(result["error"])


if __name__ == "__main__":
    unittest.main()
