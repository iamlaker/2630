import tempfile
import unittest
from pathlib import Path

from forecast_engine import CalculationRequest, InMemoryWorkbookEngine, run_forward_calculation
from input_rules import FormulaGraph, RuleService, build_formula_graph


class InputRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.service = RuleService(Path(self.temp_dir.name) / "rules.sqlite3")
        self.indicator = {
            "row": 107,
            "display_name": "对公贷款利率",
            "group": "价格假设",
            "unit": "%",
            "classification": "input",
            "year_cells": {str(year): f"{column}107" for year, column in zip(range(2026, 2031), "DEFGH")},
        }

    def tearDown(self):
        self.service.close()
        self.temp_dir.cleanup()

    def discover(self, cells, fingerprint="fp-1", version=1):
        return self.service.discover_rules(
            template_version_id=version,
            template_fingerprint=fingerprint,
            summary_sheet={"name": "汇总展示表", "index": 2},
            indicators=[self.indicator],
            graph=FormulaGraph(cells),
            actor="system",
        )[0]

    def test_traces_unique_cross_sheet_multilevel_source(self):
        cells = {}
        for year, column, source_column in zip(range(2026, 2031), "DEFGH", "CDEFG"):
            cells[("汇总展示表", f"{column}107")] = f"='中间表'!{column}9"
            cells[("中间表", f"{column}9")] = f"='信贷业务'!{source_column}19"
            cells[("信贷业务", f"{source_column}19")] = 0.0408

        rule = self.discover(cells)

        self.assertEqual(rule["confirmation_status"], "pending_confirmation")
        self.assertEqual(rule["confidence"], "high")
        self.assertEqual(rule["candidate_source_cells"][0]["sheet"], "信贷业务")
        self.assertEqual(rule["candidate_source_cells"][0]["year_cells"]["2026"], "C19")
        self.assertEqual(len(rule["formula_dependency_chain"]["2026"][0]), 3)

    def test_multiple_sources_are_not_auto_confirmed(self):
        rule = self.discover({
            ("汇总展示表", "D107"): "='参数'!A1+'参数'!B1",
            ("参数", "A1"): 1,
            ("参数", "B1"): 2,
        })
        self.assertEqual(rule["confirmation_status"], "pending_confirmation")
        self.assertEqual(rule["confidence"], "low")
        self.assertEqual(len(rule["candidate_source_cells"]), 2)

    def test_discovery_applies_requested_scale_and_percentage_defaults(self):
        scale_indicator = {
            **self.indicator,
            "row": 106,
            "display_name": "总资产",
            "group": "规模假设",
            "unit": None,
            "year_cells": {str(year): f"{column}106" for year, column in zip(range(2026, 2031), "DEFGH")},
        }
        cells = {
            ("汇总展示表", "D106"): "='参数'!A1",
            ("汇总展示表", "D107"): "='参数'!A2",
            ("参数", "A1"): 100,
            ("参数", "A2"): 0.04,
        }

        scale, percentage = self.service.discover_rules(
            1, "fp-defaults", {"name": "汇总展示表", "index": 2},
            [scale_indicator, {**self.indicator, "group": "价格假设", "unit": "%"}],
            FormulaGraph(cells), "system",
        )

        self.assertEqual((scale["display_unit"], scale["minimum_step"]), ("亿元", 1))
        self.assertEqual((percentage["display_unit"], percentage["minimum_step"]), ("%", 0.01))

    def test_cycle_and_unparseable_formula_are_diagnostic(self):
        cycle = self.discover({
            ("汇总展示表", "D107"): "='参数'!A1",
            ("参数", "A1"): "=B1",
            ("参数", "B1"): "=A1",
        })
        self.assertTrue(cycle["discovery_diagnostics"]["cycle_detected"])
        unsupported = self.discover({("汇总展示表", "D107"): "=INDIRECT(\"参数!A1\")"}, fingerprint="fp-2", version=2)
        self.assertEqual(unsupported["confirmation_status"], "unsupported")

    def test_confirm_edit_and_reject_create_versions_and_audit(self):
        rule = self.discover({
            ("汇总展示表", "D107"): "='参数'!A1",
            ("参数", "A1"): 0.04,
        })
        config = {"display_unit": "%", "adjustment_mode": "basis_point", "minimum_step": 1, "allowed_range": [0, 1000], "linkage_strategy": "same_delta"}
        confirmed = self.service.confirm_and_configure(rule["rule_id"], expected_version=1, selected_sources={"2026": {"sheet": "参数", "cell": "A1"}}, configuration=config, actor="admin")
        edited = self.service.edit_rule(confirmed["rule_id"], expected_version=2, configuration={**config, "minimum_step": 2}, actor="admin")
        rejected = self.service.reject_rule(edited["rule_id"], expected_version=3, reason="不适用", actor="admin")

        history = self.service.get_rule_history_summaries(rule["logical_rule_id"])
        self.assertEqual([item["rule_version"] for item in history], [1, 2, 3, 4])
        self.assertEqual(rejected["confirmation_status"], "rejected")
        self.assertEqual(edited["adjustment_mode"], "basis_point")
        operations = [item["operation_type"] for item in self.service.list_audit_logs()]
        self.assertIn("rule_confirmed", operations)
        self.assertIn("rule_edited", operations)
        self.assertIn("rule_rejected", operations)

    def test_compatible_rule_reuse_and_changed_formula_detection(self):
        cells = {("汇总展示表", "D107"): "='参数'!A1", ("参数", "A1"): 0.04}
        rule = self.discover(cells)
        confirmed = self.service.confirm_and_configure(rule["rule_id"], expected_version=1, selected_sources={"2026": {"sheet": "参数", "cell": "A1"}}, configuration={"display_unit": "%", "adjustment_mode": "percentage_point", "minimum_step": 0.01, "allowed_range": [0, 10], "linkage_strategy": "same_delta"}, actor="admin")
        reused = self.service.discover_rules(2, "fp-1", {"name": "汇总展示表", "index": 2}, [self.indicator], FormulaGraph(cells), "system")[0]
        self.assertEqual(reused["confirmation_status"], "confirmed")
        self.assertEqual(reused["reused_from_rule_id"], confirmed["rule_id"])
        self.assertEqual(reused["adjustment_mode"], "percentage_point")
        self.assertEqual(reused["allowed_range"], [0, 10])

        changed = self.service.discover_rules(3, "fp-2", {"name": "汇总展示表", "index": 2}, [self.indicator], FormulaGraph({
            ("汇总展示表", "D107"): "='参数'!B1", ("参数", "B1"): 0.05,
        }), "system")[0]
        self.assertEqual(changed["confirmation_status"], "changed")

    def test_forward_calculation_blocks_pending_and_consumes_confirmed_rule(self):
        rule = self.discover({("汇总展示表", "D107"): "='参数'!A1", ("参数", "A1"): 0.04})
        engine = InMemoryWorkbookEngine()
        blocked = run_forward_calculation(engine, CalculationRequest({2026: 0.05}, rule_record=rule))
        self.assertEqual(blocked["calculation_status"], "pending_rule_confirmation")
        self.assertEqual(blocked["pending_rules"][0]["indicator"], "对公贷款利率")

        confirmed = self.service.confirm_and_configure(rule["rule_id"], expected_version=1, selected_sources={"2026": {"sheet": "参数", "cell": "A1"}}, configuration={"display_unit": "%", "adjustment_mode": "basis_point", "minimum_step": 1, "allowed_range": [0, 10], "linkage_strategy": "independent"}, actor="admin")
        result = run_forward_calculation(InMemoryWorkbookEngine(), CalculationRequest({2026: 0.05}, rule_record=confirmed))
        self.assertEqual(result["calculation_status"], "valid")

    def test_formula_graph_can_be_built_from_engine_boundary(self):
        class FormulaEngine:
            cells = {
                ("汇总展示表", "D107"): "='参数'!A1",
                ("参数", "A1"): 0.04,
            }

            def read_cell_formula_or_value(self, sheet, cell):
                return self.cells.get((sheet, cell))

        graph = build_formula_graph(FormulaEngine(), [self.indicator], {"name": "汇总展示表", "index": 2})
        self.assertEqual(graph.trace("汇总展示表", "D107")["candidates"][0]["cell"], "A1")

    def test_calculation_block_is_audited(self):
        rule = self.discover({("汇总展示表", "D107"): "='参数'!A1", ("参数", "A1"): 0.04})
        self.assertEqual(rule["confirmation_status"], "pending_confirmation")

    def test_rule_summary_index_supports_latest_list_without_payload_scan(self):
        rule = self.discover({("汇总展示表", "D107"): "='参数'!A1", ("参数", "A1"): 0.04})
        summaries = self.service.list_latest_rule_summaries(1)
        self.assertEqual(summaries[0]["rule_id"], rule["rule_id"])
        self.assertEqual(summaries[0]["display_name"], "对公贷款利率")
        self.assertTrue(summaries[0]["display_name"])

    def test_rebuild_summary_index_migrates_existing_rules(self):
        rule = self.discover({("汇总展示表", "D107"): "='参数'!A1", ("参数", "A1"): 0.04})
        self.assertEqual(self.service.list_latest_rule_summaries(1)[0]["rule_id"], rule["rule_id"])

    def test_rule_review_omits_heavy_reference_paths_and_limits_formula_chains(self):
        cells = {("汇总展示表", "D107"): "='参数'!A1+'参数'!B1", ("参数", "A1"): 1, ("参数", "B1"): 2}
        rule = self.discover(cells)
        review = self.service.get_rule_review(rule["rule_id"], chain_limit=1)
        self.assertNotIn("reference_paths", review["candidate_source_cells"][0])
        self.assertEqual(len(review["formula_dependency_chain"]["2026"]), 1)
        self.assertEqual(review["formula_chain_counts"]["2026"], 2)

    def test_rule_history_summaries_do_not_return_formula_chains(self):
        rule = self.discover({("汇总展示表", "D107"): "='参数'!A1", ("参数", "A1"): 0.04})
        history = self.service.get_rule_history_summaries(rule["logical_rule_id"])
        self.assertNotIn("formula_dependency_chain", history[0])

    def test_new_rule_version_reuses_unchanged_heavy_structures(self):
        rule = self.discover({("汇总展示表", "D107"): "='参数'!A1", ("参数", "A1"): 0.04})
        before = self.service.connection.execute("SELECT COUNT(*) FROM discovery_snapshots").fetchone()[0]
        self.service.edit_rule(rule["rule_id"], expected_version=1, configuration={"display_unit": "%", "adjustment_mode": "basis_point", "minimum_step": 1, "allowed_range": [0, 10], "linkage_strategy": "independent"}, actor="admin")
        self.assertEqual(self.service.connection.execute("SELECT COUNT(*) FROM discovery_snapshots").fetchone()[0], before)

    def test_graph_build_continues_when_a_referenced_cell_cannot_be_read(self):
        class FormulaEngine:
            def read_cell_formula_or_value(self, sheet, cell):
                if sheet == "汇总展示表":
                    return "='不存在的表'!A1"
                raise RuntimeError("worksheet missing")

        graph = build_formula_graph(FormulaEngine(), [self.indicator], {"name": "汇总展示表", "index": 2})
        trace = graph.trace("汇总展示表", "D107")
        self.assertTrue(trace["read_error"])
        self.assertEqual(trace["candidates"], [])

    def test_processed_formula_keeps_unique_candidate_but_lowers_confidence(self):
        cells = {}
        for year, column in zip(range(2026, 2031), "DEFGH"):
            cells[("汇总展示表", f"{column}107")] = f"='参数'!{column}1*1.1+0.01"
            cells[("参数", f"{column}1")] = 0.04
        rule = self.discover(cells)
        self.assertEqual(len(rule["candidate_source_cells"]), 1)
        self.assertTrue(rule["discovery_diagnostics"]["processed_formula"])
        self.assertEqual(rule["confidence"], "low")
        self.assertEqual(rule["confirmation_status"], "pending_confirmation")

    def test_nested_processing_functions_keep_all_leaf_candidates(self):
        trace = FormulaGraph({
            ("汇总展示表", "D107"): "=IFERROR(ROUND('参数'!A1/'参数'!B1,4),0)",
            ("参数", "A1"): 120,
            ("参数", "B1"): 100,
        }).trace("汇总展示表", "D107")
        self.assertEqual({item["cell"] for item in trace["candidates"]}, {"A1", "B1"})
        self.assertTrue(trace["processed_formula"])
        self.assertFalse(trace["unparseable"])

    def test_deep_processed_chain_reaches_leaf_without_auto_confirmation(self):
        cells = {("汇总展示表", "D107"): "=ROUND('中间1'!A1*100,2)"}
        for depth in range(1, 8):
            sheet = f"中间{depth}"
            cells[(sheet, "A1")] = f"='中间{depth + 1}'!A1+0" if depth < 7 else "='参数'!A1"
        cells[("参数", "A1")] = 0.04
        trace = FormulaGraph(cells).trace("汇总展示表", "D107")
        self.assertEqual(trace["candidates"][0]["cell"], "A1")
        self.assertEqual(trace["max_depth"], 9)
        self.assertTrue(trace["processed_formula"])


if __name__ == "__main__":
    unittest.main()
