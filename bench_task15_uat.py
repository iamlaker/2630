"""Task15 HTTP UAT and mixed warm_com endurance benchmark."""
from __future__ import annotations

import csv
import io
import json
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_URL = "http://127.0.0.1:8765"
EXPECTED_SHA256 = "a27df7cb03878ea11779e82d1c7eca3b45abf227e6bddd6d269d77b7d62fbdee"
EXPECTED_PUBLICATION = "aa4f9fd2-c399-4141-882c-cb43fa5a9708"


def request(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{method} {path}: {exc.read().decode(errors='replace')}") from exc


def wait_task(task_id: str, cancel_after: float | None = None) -> tuple[dict, float]:
    started = time.perf_counter()
    cancel_sent = False
    while True:
        task = request("GET", f"/api/calculations/{task_id}")
        elapsed = time.perf_counter() - started
        if cancel_after is not None and elapsed >= cancel_after and not cancel_sent:
            request("POST", f"/api/calculations/{task_id}/cancel", {})
            cancel_sent = True
        if task["status"] not in {"queued", "running", "cancelling", "cancel_requested"}:
            return task, elapsed
        time.sleep(.1)


def excel_pids() -> list[int]:
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq EXCEL.EXE", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, check=False, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return sorted(int(row[1]) for row in csv.reader(io.StringIO(completed.stdout)) if len(row) > 1 and row[0].casefold() == "excel.exe")


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * fraction + .999999) - 1))]


def diagnostic(task: dict, wall_s: float, kind: str) -> dict:
    result = task.get("result") or {}
    details = result.get("calculation_details") or {}
    progress = task.get("progress") or {}
    trust = result.get("trust") or {}
    return {
        "kind": kind, "task_id": task["task_id"], "task_status": task["status"], "wall_s": round(wall_s, 3),
        "search_count": details.get("search_count"), "requested_engine_mode": details.get("engine_mode_requested") or task.get("engine_mode"),
        "actual_engine_mode": details.get("engine_mode") or progress.get("engine_mode"),
        "worker_id": details.get("worker_id") or progress.get("worker_id"),
        "queue_wait_ms": details.get("queue_wait_ms", progress.get("queue_wait_ms")),
        "stage_timings": details.get("stage_timings") or progress.get("stage_timings") or {},
        "fallback_reason": details.get("fallback_reason") or progress.get("fallback_reason"),
        "validation_state": trust.get("status"), "cancellation_state": task.get("cancel_status"),
        "feasible": result.get("feasible"), "error": task.get("error"),
    }


def main() -> None:
    workbench = request("GET", "/api/workbench")
    if workbench["template"]["fingerprint"].casefold() != EXPECTED_SHA256:
        raise RuntimeError("activity template fingerprint mismatch")
    if workbench["rule_set"]["publication_id"] != EXPECTED_PUBLICATION:
        raise RuntimeError("activity publication mismatch")
    parameters = {item["name"]: item for item in workbench["parameters"]}
    bond = parameters["10年期国债收益率"]
    ac_income = parameters["AC类投资收益"]
    profit = next(item for item in workbench["details"] if item["name"] == "归母净利润")
    template_id = workbench["template"]["id"]
    pids_before = excel_pids()

    adjustment = {"rule_id": bond["rule"]["rule_id"], "indicator_id": bond["id"], "values": {"2030": .0185}}
    forward_body = {"template_version_id": template_id, "adjustments": [adjustment], "engine_mode": "warm_com"}
    v1_body = {
        "template_version_id": template_id, "engine_mode": "warm_com", "adjustments": [], "max_evaluations": 3,
        "variable": {"rule_id": bond["rule"]["rule_id"], "indicator_id": bond["id"], "year": "2030", "lower": .0165, "upper": .0185},
        "constraints": [{"indicator_name": "归母净利润", "year": "2030", "kind": "min", "value": profit["values"]["2030"] - 1, "hard": True, "enabled": True, "indicator_type": "output"}],
    }
    v2_body = {
        "template_version_id": template_id, "engine_mode": "warm_com", "adjustments": [], "max_evaluations": 3,
        "variables": [
            {"rule_id": bond["rule"]["rule_id"], "indicator_id": bond["id"], "year": "2030", "priority": 1, "lower": .0165, "upper": .0185, "candidates": [.0175, .0185]},
            {"rule_id": ac_income["rule"]["rule_id"], "indicator_id": ac_income["id"], "year": "2030", "priority": 2, "lower": 35, "upper": 40, "candidates": [35, 40]},
        ],
        "constraints": [{"indicator_name": "归母净利润", "year": "2030", "kind": "min", "value": profit["values"]["2030"] - 1, "hard": True, "enabled": True, "indicator_type": "output"}],
    }

    uat = []
    for kind, path, body in (("forward", "/api/calculations", forward_body), ("reverse_v1", "/api/reverse-calculations", v1_body), ("reverse_v2", "/api/reverse-calculations", v2_body)):
        created = request("POST", path, body)
        task, wall = wait_task(created["task_id"])
        uat.append(diagnostic(task, wall, kind))
    forward_result = request("GET", f"/api/calculations/{uat[0]['task_id']}")["result"]
    scenario_payload = {"name": "Task15-UAT-API", **forward_result["scenario_draft"]}
    scenario_a = request("POST", "/api/scenarios", scenario_payload)
    scenario_b = request("POST", f"/api/scenarios/{scenario_a['scenario_id']}/copy", {"name": "Task15-UAT-API-Copy"})
    comparison_body = {"scenario_ids": [scenario_a["scenario_id"], scenario_b["scenario_id"]], "baseline_scenario_id": scenario_a["scenario_id"], "force_refresh": True, "engine_mode": "warm_com"}
    comparison_created = request("POST", "/api/comparisons", comparison_body)
    comparison_task, comparison_wall = wait_task(comparison_created["task_id"])
    uat.append(diagnostic(comparison_task, comparison_wall, "comparison"))
    exports = {
        "scenario": request("POST", "/api/exports/scenario", {**workbench, "scenario_draft": forward_result["scenario_draft"], "metadata": {**forward_result["scenario_draft"], "template_fingerprint": EXPECTED_SHA256, "scenario_id": scenario_a["scenario_id"], "calculation_time": forward_result["calculation_details"]["finished_at"]}}),
        "reverse": request("POST", "/api/exports/reverse", (lambda result: {**result, "metadata": {**result["scenario_draft"], "template_fingerprint": EXPECTED_SHA256, "scenario_id": f"reverse:{result['calculation_details']['calculation_id']}", "calculation_time": result["calculation_details"]["finished_at"]}})(request("GET", f"/api/calculations/{uat[1]['task_id']}")["result"])),
        "comparison": request("POST", "/api/exports/comparison", comparison_task["result"]),
    }

    cancel_created = request("POST", "/api/reverse-calculations", {**v1_body, "max_evaluations": 20, "constraints": [{**v1_body["constraints"][0], "value": profit["values"]["2030"] + 1000}]})
    cancelled_task, cancel_wall = wait_task(cancel_created["task_id"], cancel_after=.15)
    cancellation = diagnostic(cancelled_task, cancel_wall, "reverse_v1_cancel")

    runs = []
    schedule = ["forward"] * 12 + ["reverse_v1"] * 6 + ["reverse_v2"] * 6 + ["comparison"] * 6
    for index, kind in enumerate(schedule, 1):
        if kind == "forward":
            body = {**forward_body, "adjustments": [{**adjustment, "values": {"2030": .016 + (index % 10) * .0005}}]}
            created = request("POST", "/api/calculations", body)
        elif kind == "reverse_v1":
            created = request("POST", "/api/reverse-calculations", v1_body)
        elif kind == "reverse_v2":
            created = request("POST", "/api/reverse-calculations", v2_body)
        else:
            created = request("POST", "/api/comparisons", comparison_body)
        task, wall = wait_task(created["task_id"])
        runs.append(diagnostic(task, wall, kind))

    deadline = time.time() + 5
    pids_after = excel_pids()
    while set(pids_after) - set(pids_before) and time.time() < deadline:
        time.sleep(.25)
        pids_after = excel_pids()
    durations = [run["wall_s"] for run in runs]
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(), "base_url": BASE_URL,
        "template_fingerprint": EXPECTED_SHA256, "publication_id": EXPECTED_PUBLICATION,
        "browser_uat": {"forward_scenario_name": "Task15-UAT-Forward", "observed": True},
        "api_uat": uat, "exports": exports, "cancellation": cancellation,
        "mixed_30": {
            "count": len(runs), "distribution": {kind: schedule.count(kind) for kind in sorted(set(schedule))},
            "p50_s": round(statistics.median(durations), 3), "p95_s": round(percentile(durations, .95), 3), "p99_s": round(percentile(durations, .99), 3),
            "total_forward_calls": sum((run["search_count"] or (2 if run["kind"] == "comparison" else 1)) for run in runs),
            "all_succeeded": all(run["task_status"] == "succeeded" for run in runs), "runs": runs,
        },
        "excel_pids_before": pids_before, "excel_pids_after": pids_after, "new_orphan_excel_pids": sorted(set(pids_after) - set(pids_before)),
    }
    out = ROOT / ".scratch" / "perf" / f"task15-warm-uat-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(out), "uat": uat, "cancellation": cancellation, "mixed_30": {key: value for key, value in report["mixed_30"].items() if key != "runs"}, "new_orphans": report["new_orphan_excel_pids"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
