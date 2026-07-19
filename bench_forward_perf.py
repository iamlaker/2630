"""任务 13 性能基准：真实 0717 活动 publication + confirmed 规则「10年期国债收益率」2026 单年 +10bp。

只读使用规则库与模板目录；计算在隔离临时副本上执行，不修改原始模板。
用法：python bench_forward_perf.py
输出：stdout 打印摘要，完整 JSON 写入 .scratch/perf/bench-<timestamp>.json
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parent
DATA = ROOT / ".workbench"
sys.path.insert(0, str(ROOT))

from forecast_engine import (  # noqa: E402
    CalculationRequest,
    ConfirmedInputRule,
    ExcelComWorkbookEngine,
    run_forward_calculation,
)
from rule_store import RuleStore  # noqa: E402
from template_catalog import TemplateImportService  # noqa: E402

INDICATOR_NAME = "10年期国债收益率"
ADJUSTED_VALUES = {2026: 0.0185, 2027: 0.0175, 2028: 0.0175, 2029: 0.0175, 2030: 0.0175}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_sample():
    rules = RuleStore(DATA / "rules.sqlite3")
    templates = TemplateImportService(DATA / "templates", DATA / "catalog.sqlite3", ExcelComWorkbookEngine())
    try:
        versions = templates.list_template_versions()
        template = next(item for item in versions if item["template_fingerprint"].casefold() == __import__("workbench").ACTIVITY_TEMPLATE_FINGERPRINT.casefold())
        template_version_id = template["template_version_id"]
        publication = rules.get_active_publication(template_version_id)
        assert publication, "没有活动 publication"
        published = rules.get_active_publication_rules(template_version_id)
        rule = next(item for item in published if item["display_name"] == INDICATOR_NAME)
        sources = rule["confirmed_source_cells"]
        if isinstance(sources, dict):
            sources = [{"year": year, **source} for year, source in sources.items()]
        sheets = {item["sheet"] for item in sources}
        assert len(sheets) == 1
        confirmed = ConfirmedInputRule(rule["display_name"], next(iter(sheets)), {int(item["year"]): item["cell"] for item in sources})
        template = templates.get_indicator_catalog(template_version_id)
        catalog_baseline = {
            item["display_name"]: item["year_values"]
            for item in template["indicator_catalog"]
            if item.get("classification") == "output"
        }
        return {
            "publication_id": publication["publication_id"],
            "rule": confirmed,
            "template_path": DATA / "templates" / template["storage_id"],
            "template_fingerprint": template["template_fingerprint"],
            "catalog_baseline": catalog_baseline,
        }
    finally:
        rules.close()
        templates.close()


def compare_snapshots(reference: dict, candidate: dict) -> dict:
    ref_keys, cand_keys = set(reference), set(candidate)
    max_abs = 0.0
    mismatched = []
    for key in ref_keys & cand_keys:
        for year in ("2026", "2027", "2028", "2029", "2030"):
            left, right = reference[key].get(year), candidate[key].get(year)
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                diff = abs(float(left) - float(right))
                max_abs = max(max_abs, diff)
                if diff > 1e-9:
                    mismatched.append((key, year, diff))
            elif left != right:
                mismatched.append((key, year, "type"))
    return {
        "keys_equal": ref_keys == cand_keys,
        "only_in_reference": sorted(ref_keys - cand_keys),
        "only_in_candidate": sorted(cand_keys - ref_keys),
        "max_abs_diff": max_abs,
        "mismatch_count": len(mismatched),
        "mismatch_sample": mismatched[:5],
    }


def legacy_read_summary(engine: ExcelComWorkbookEngine) -> dict:
    """任务 05 时期的逐单元格读取，用于对照批量读取成本。"""
    sheet = engine._summary_worksheet()
    indicators = {}
    for row in range(1, sheet.UsedRange.Rows.Count + 1):
        name = engine._text(sheet.Cells(row, 2).Value) or engine._text(sheet.Cells(row, 1).Value)
        values = [sheet.Cells(row, column).Value for column in range(4, 9)]
        if name and any(value is not None for value in values):
            indicators[name] = {str(year): value for year, value in zip(range(2026, 2031), values)}
    return indicators


def run_variant(sample: dict, *, recalc_mode: str, read_baseline: bool) -> dict:
    engine = ExcelComWorkbookEngine(recalc_mode=recalc_mode)
    request = CalculationRequest(input_adjustments=[{"input_rule": sample["rule"], "values": ADJUSTED_VALUES}])
    started = time.perf_counter()
    result = run_forward_calculation(engine, request, template_path=sample["template_path"], read_baseline=read_baseline)
    total = time.perf_counter() - started
    return {
        "recalc_mode": recalc_mode,
        "read_baseline": read_baseline,
        "total_s": round(total, 2),
        "status": result["calculation_status"],
        "iterations": result["iterations"],
        "final_differences": result["final_differences"],
        "snapshot_count": len(result["output_indicators"]),
        "stage_timings": result["stage_timings"],
        "outputs": result["output_indicators"],
        "summary_before": result["summary_before"],
    }


def probe_summary_read(sample: dict) -> dict:
    engine = ExcelComWorkbookEngine()
    try:
        engine.open_isolated(sample["template_path"])
        started = time.perf_counter()
        legacy = legacy_read_summary(engine)
        legacy_s = time.perf_counter() - started
        started = time.perf_counter()
        batch = engine.read_summary()
        batch_s = time.perf_counter() - started
        comparison = compare_snapshots(legacy, batch)
        return {
            "legacy_per_cell_s": round(legacy_s, 3),
            "batch_range_s": round(batch_s, 3),
            "legacy_count": len(legacy),
            "batch_count": len(batch),
            "identical": comparison["keys_equal"] and comparison["max_abs_diff"] == 0 and comparison["mismatch_count"] == 0,
            "comparison": comparison,
        }
    finally:
        engine.close()


def main() -> None:
    sample = load_sample()
    template_sha_before = sha256(sample["template_path"])
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "publication_id": sample["publication_id"],
        "template_fingerprint": sample["template_fingerprint"],
        "template_sha256_before": template_sha_before,
        "indicator": INDICATOR_NAME,
        "adjustment": {str(k): v for k, v in ADJUSTED_VALUES.items()},
    }
    print(f"publication={sample['publication_id']} template={sample['template_path'].name}")

    print("\n[1/5] 变体 full_rebuild + read_baseline（当前生产行为）...")
    runs = {"full_rebuild": run_variant(sample, recalc_mode="full_rebuild", read_baseline=True)}
    print(f"  total={runs['full_rebuild']['total_s']}s status={runs['full_rebuild']['status']} iterations={runs['full_rebuild']['iterations']}")

    print("\n[2/5] 变体 CalculateFull ...")
    runs["full"] = run_variant(sample, recalc_mode="full", read_baseline=True)
    print(f"  total={runs['full']['total_s']}s status={runs['full']['status']} iterations={runs['full']['iterations']}")

    print("\n[3/5] 变体 Calculate（普通）...")
    runs["normal"] = run_variant(sample, recalc_mode="normal", read_baseline=True)
    print(f"  total={runs['normal']['total_s']}s status={runs['normal']['status']} iterations={runs['normal']['iterations']}")

    print("\n[4/5] 变体 full_rebuild + 跳过基准读取 ...")
    runs["skip_baseline"] = run_variant(sample, recalc_mode="full_rebuild", read_baseline=False)
    print(f"  total={runs['skip_baseline']['total_s']}s status={runs['skip_baseline']['status']} iterations={runs['skip_baseline']['iterations']}")

    print("\n[5/5] read_summary 逐单元格 vs 批量 Range 探针 ...")
    probe = probe_summary_read(sample)
    print(f"  legacy={probe['legacy_per_cell_s']}s batch={probe['batch_range_s']}s identical={probe['identical']}")

    reference = runs["full_rebuild"]
    comparisons = {}
    for name in ("full", "normal", "skip_baseline"):
        comparisons[f"{name}_vs_full_rebuild"] = compare_snapshots(reference["outputs"], runs[name]["outputs"])
        comparisons[f"{name}_vs_full_rebuild"]["final_differences"] = runs[name]["final_differences"]
        comparisons[f"{name}_vs_full_rebuild"]["iterations"] = runs[name]["iterations"]
        comparisons[f"{name}_vs_full_rebuild"]["status"] = runs[name]["status"]
    comparisons["catalog_vs_excel_baseline"] = compare_snapshots(reference["summary_before"], sample["catalog_baseline"])

    for run in runs.values():
        run.pop("outputs")
        run.pop("summary_before")
    report.update({
        "runs": runs,
        "summary_read_probe": probe,
        "comparisons": comparisons,
        "template_sha256_after": sha256(sample["template_path"]),
        "template_unchanged": sha256(sample["template_path"]) == template_sha_before,
    })
    out_dir = ROOT / ".scratch" / "perf"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"bench-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完整报告: {out_file}")
    print(f"模板未修改: {report['template_unchanged']}")
    print("\n== 对比 ==")
    for name, comparison in comparisons.items():
        print(f"  {name}: keys_equal={comparison['keys_equal']} max_abs_diff={comparison['max_abs_diff']} mismatches={comparison['mismatch_count']}")


if __name__ == "__main__":
    main()
