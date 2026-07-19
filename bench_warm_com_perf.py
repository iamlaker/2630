"""Task14 real Excel COM acceptance: cold vs warm, isolation, timing, and orphan checks."""
from __future__ import annotations

import csv
import io
import json
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from bench_forward_perf import ADJUSTED_VALUES, compare_snapshots, load_sample, sha256
from forecast_engine import CalculationRequest, ExcelComWorkbookEngine, WarmExcelWorker, run_forward_calculation


ROOT = Path(__file__).resolve().parent
ORIGINAL_TEMPLATE = ROOT / "模版" / "2026-2030年盈利测算表0717-模板.xlsx"
EXPECTED_SHA256 = "a27df7cb03878ea11779e82d1c7eca3b45abf227e6bddd6d269d77b7d62fbdee"


def excel_pids() -> list[int]:
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq EXCEL.EXE", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, check=False, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return sorted(
        int(row[1]) for row in csv.reader(io.StringIO(completed.stdout))
        if len(row) > 1 and row[0].casefold() == "excel.exe"
    )


def request(sample: dict, value: float) -> CalculationRequest:
    return CalculationRequest(input_adjustments=[{
        "input_rule": sample["rule"], "values": {**ADJUSTED_VALUES, 2026: value},
    }])


def compact(result: dict, duration: float) -> dict:
    return {
        "duration_s": round(duration, 3), "status": result["calculation_status"],
        "iterations": result["iterations"], "final_differences": result["final_differences"],
        "engine_mode": result.get("engine_mode"), "worker_id": result.get("worker_id"),
        "queue_wait_ms": result.get("queue_wait_ms"), "stage_timings": result["stage_timings"],
    }


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * percentage + .999999) - 1))]


def main() -> None:
    sample = load_sample()
    if sample["template_fingerprint"].casefold() != EXPECTED_SHA256:
        raise RuntimeError(f"unexpected activity fingerprint: {sample['template_fingerprint']}")
    original_before = sha256(ORIGINAL_TEMPLATE)
    activity_copy_before = sha256(sample["template_path"])
    if activity_copy_before.casefold() != EXPECTED_SHA256:
        raise RuntimeError(f"unexpected catalog activity template SHA-256: {activity_copy_before}")
    pids_before = excel_pids()

    started = time.perf_counter()
    cold_result = run_forward_calculation(
        ExcelComWorkbookEngine(), request(sample, .0185), template_path=sample["template_path"],
    )
    cold_duration = time.perf_counter() - started

    worker = WarmExcelWorker(EXPECTED_SHA256, timeout_seconds=60)
    warm_results = []
    try:
        for value in [.0185, .0160, .0165, .0170, .0175, .0180, .0190, .0195, .0200, .0205]:
            started = time.perf_counter()
            result = worker.calculate(request(sample, value), template_path=sample["template_path"])
            duration = time.perf_counter() - started
            warm_results.append({"input_2026": value, "result": result, "run": compact(result, duration)})
        repeated = worker.calculate(request(sample, .0185), template_path=sample["template_path"])
    finally:
        worker.shutdown()

    deadline = time.time() + 5
    pids_after = excel_pids()
    while set(pids_after) - set(pids_before) and time.time() < deadline:
        time.sleep(.25)
        pids_after = excel_pids()
    continuous = [item["run"]["duration_s"] for item in warm_results[1:]]
    cold_warm = compare_snapshots(cold_result["output_indicators"], warm_results[0]["result"]["output_indicators"])
    repeat_check = compare_snapshots(warm_results[0]["result"]["output_indicators"], repeated["output_indicators"])
    unique_profitability = [
        item["result"]["output_indicators"].get("归母净利润", {}).get("2026")
        for item in warm_results
    ]
    snapshot_signatures = [
        json.dumps(item["result"]["output_indicators"], ensure_ascii=False, sort_keys=True, default=str)
        for item in warm_results
    ]
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "publication_id": sample["publication_id"], "template_fingerprint": sample["template_fingerprint"],
        "template_sha256_before": original_before, "template_sha256_after": sha256(ORIGINAL_TEMPLATE),
        "template_unchanged": sha256(ORIGINAL_TEMPLATE) == original_before,
        "original_path_matches_activity_fingerprint": original_before.casefold() == EXPECTED_SHA256,
        "catalog_activity_copy_sha256_before": activity_copy_before,
        "catalog_activity_copy_sha256_after": sha256(sample["template_path"]),
        "catalog_activity_copy_unchanged": sha256(sample["template_path"]) == activity_copy_before,
        "excel_pids_before": pids_before, "excel_pids_after": pids_after,
        "new_orphan_excel_pids": sorted(set(pids_after) - set(pids_before)),
        "cold": compact(cold_result, cold_duration),
        "warm_first": warm_results[0]["run"],
        "warm_continuous": {
            "count": len(continuous), "median_s": round(statistics.median(continuous), 3),
            "p95_s": round(percentile(continuous, .95), 3), "runs": [item["run"] for item in warm_results[1:]],
        },
        "cold_warm_comparison": cold_warm,
        "state_isolation": {
            "different_input_count": len(warm_results), "inputs": [item["input_2026"] for item in warm_results],
            "profitability_2026": unique_profitability, "first_request_repeat_comparison": repeat_check,
            "distinct_output_snapshot_count": len(set(snapshot_signatures)),
            "passed": len(set(snapshot_signatures)) > 1 and repeat_check["keys_equal"] and repeat_check["mismatch_count"] == 0,
        },
        "cycle_within_tolerance": all(max(item["result"]["final_differences"].values()) <= .1 for item in warm_results),
    }
    for item in warm_results:
        item.pop("result")
    out_dir = ROOT / ".scratch" / "perf"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"warm-com-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out_file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "report": str(out_file), "cold_s": report["cold"]["duration_s"],
        "warm_first_s": report["warm_first"]["duration_s"],
        "warm_median_s": report["warm_continuous"]["median_s"], "warm_p95_s": report["warm_continuous"]["p95_s"],
        "cold_warm_max_abs_diff": cold_warm["max_abs_diff"], "state_isolation": report["state_isolation"]["passed"],
        "template_unchanged": report["template_unchanged"], "new_orphans": report["new_orphan_excel_pids"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
