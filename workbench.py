from __future__ import annotations

import argparse
import json
import mimetypes
import os
import secrets
import re
import threading
import time
import uuid
import subprocess
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from forecast_engine import CalculationCancelled, CalculationRequest, ConfirmedInputRule, ExcelComWorkbookEngine, TEMPLATE_PATH, WarmExcelWorker, WarmExcelWorkerError, run_forward_calculation
from input_rules import RuleService, build_formula_graph
from scenario_store import SCENARIO_TYPES, ScenarioStore
from template_catalog import TemplateImportService
from reverse_calculation import ReverseConstraint, evaluate_constraints, search_priority_variables, search_single_variable, variable_candidates
from export_service import ExportService


YEARS = tuple(range(2026, 2031))
PENDING_STATUSES = {"pending_confirmation", "changed", "rejected", "unsupported"}
CORE_ALIASES = {
    "利润": ("归母净利润", "净利润", "利润"),
    "营业收入": ("营业收入", "营业净收入", "营收"),
    "净息差": ("净息差", "净利息收入", "利息净收入"),
    "总资产": ("并表口径总资产", "资产总额", "总资产"),
    "ROE / RAROC": ("ROE", "净资产收益率", "RAROC"),
    "资本充足率": ("资本充足率", "核心一级资本充足率"),
    "RWA": ("风险加权资产", "RWA"),
    "LCR": ("流动性覆盖率", "LCR"),
    "NSFR": ("净稳定资金比例", "NSFR"),
}
ACTIVITY_TEMPLATE_FINGERPRINT = "a27df7cb03878ea11779e82d1c7eca3b45abf227e6bddd6d269d77b7d62fbdee"


def filter_parameters(items: list[dict[str, Any]], *, search: str = "", group: str | None = None, favorites: bool = False, adjusted: bool = False, pending: bool = False) -> list[dict[str, Any]]:
    search = search.casefold().strip()
    return [item for item in items if (not search or search in item["name"].casefold()) and (not group or item["group"] == group) and (not favorites or item.get("favorite")) and (not adjusted or item.get("adjusted")) and (not pending or item.get("rule_status") in PENDING_STATUSES)]


def apply_linkage(baseline: dict[str, float], year: int, value: float, strategy: str) -> dict[str, float]:
    target = str(year)
    if target not in baseline:
        raise ValueError(f"不支持年度 {year}")
    values = dict(baseline)
    if strategy == "independent":
        values[target] = value
    elif strategy == "same_delta":
        delta = value - baseline[target]
        values = {key: current + delta for key, current in baseline.items()}
    elif strategy == "same_value":
        values = {key: value for key in baseline}
    elif strategy == "baseline_ratio":
        if baseline[target] == 0:
            raise ValueError("基准值为零，无法按比例联动")
        ratio = value / baseline[target]
        values = {key: current * ratio for key, current in baseline.items()}
    else:
        raise ValueError("未知五年联动策略")
    return values


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CalculationTaskManager:
    """异步正向测算任务：queued → running → (cancel_requested) → succeeded/failed/cancelled/cycle_not_converged。

    取消为 best-effort：cancel() 只置令牌，引擎在阶段边界和循环迭代边界检查，
    处于不可中断 COM 调用期间任务保持 cancel_requested，不伪造已取消。
    """

    TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "cycle_not_converged"}
    MAX_TASKS = 100

    def __init__(self):
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(self, service: "WorkbenchService", template_version_id: int, adjustments: Any, on_complete: Any = None, runner: Any = None, engine_mode: str | None = None) -> dict[str, Any]:
        task_id = str(uuid.uuid4())
        cancel_token = threading.Event()
        task = {
            "task_id": task_id, "status": "queued", "created_at": _utc_now(), "started_at": None, "finished_at": None,
            "template_version_id": template_version_id, "current_stage": None, "stage_timings": {},
            "iterations": 0, "final_differences": {}, "engine_mode": engine_mode, "worker_id": None, "queue_wait_ms": 0,
            "cancel_requested": False, "cancel_status": "not_requested", "error": None, "result": None,
            "_cancel_token": cancel_token, "_on_complete": on_complete, "_runner": runner,
        }
        with self._lock:
            self._tasks[task_id] = task
            self._evict_locked()
        thread = threading.Thread(target=self._run, args=(task, service, template_version_id, adjustments, cancel_token), daemon=True)
        with self._lock:
            task["_thread"] = thread
        thread.start()
        return self.snapshot(task_id)

    def _run(self, task: dict[str, Any], service: "WorkbenchService", template_version_id: int, adjustments: list[dict[str, Any]], cancel_token: threading.Event) -> None:
        with self._lock:
            if task["status"] == "queued":
                task["status"] = "running"
            task["started_at"] = _utc_now()

        def progress(stage: str, timings: dict[str, Any]) -> None:
            with self._lock:
                task["current_stage"] = stage
                task["stage_timings"] = timings

        try:
            runner = task.get("_runner") or service.calculate
            result = runner(template_version_id, adjustments, cancel_token=cancel_token, progress=progress)
            trust = result.get("trust", {})
            details = result.get("calculation_details", {})
            with self._lock:
                task["result"] = result
                task["iterations"] = details.get("iterations", 0)
                task["final_differences"] = details.get("final_differences", {})
                task["stage_timings"] = details.get("stage_timings", {})
                task["engine_mode"] = details.get("engine_mode", task.get("engine_mode"))
                task["worker_id"] = details.get("worker_id")
                task["queue_wait_ms"] = details.get("queue_wait_ms", 0)
                task["cancel_status"] = details.get("cancel_status", "not_requested")
                task["status"] = result.get("task_status") or {"valid": "succeeded", "reverse_no_feasible": "succeeded", "cycle_not_converged": "cycle_not_converged"}.get(trust.get("status"), "failed")
                if task["status"] == "failed":
                    task["error"] = trust.get("error") or trust.get("reason")
        except CalculationCancelled:
            with self._lock:
                task["status"] = "cancelled"
                task["result"] = None
        except Exception as exc:
            with self._lock:
                task["status"] = "failed"
                task["error"] = str(exc)
        finally:
            with self._lock:
                task["finished_at"] = _utc_now()
            callback = task.get("_on_complete")
            if callback is not None:
                try:
                    callback(self.snapshot(task["task_id"]))
                except Exception as exc:
                    with self._lock:
                        task["completion_error"] = str(exc)

    def _evict_locked(self) -> None:
        terminal = [task for task in self._tasks.values() if task["status"] in self.TERMINAL_STATUSES]
        if len(self._tasks) <= self.MAX_TASKS:
            return
        for task in sorted(terminal, key=lambda item: item["finished_at"] or "")[: len(self._tasks) - self.MAX_TASKS]:
            self._tasks.pop(task["task_id"], None)

    def _get(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise ValueError("测算任务不存在或已被清理")
            return task

    def cancel(self, task_id: str) -> dict[str, Any]:
        task = self._get(task_id)
        with self._lock:
            if task["status"] in ("queued", "running", "cancel_requested"):
                task["cancel_requested"] = True
                task["cancel_status"] = "requested"
                task["status"] = "cancel_requested"
                task["_cancel_token"].set()
        return self.snapshot(task_id)

    def snapshot(self, task_id: str) -> dict[str, Any]:
        task = self._get(task_id)
        with self._lock:
            public = {key: value for key, value in task.items() if not key.startswith("_")}
        started = task.get("started_at")
        end = task.get("finished_at") or _utc_now()
        if started:
            public["elapsed_ms"] = round((datetime.fromisoformat(end) - datetime.fromisoformat(started)).total_seconds() * 1000, 2)
        else:
            public["elapsed_ms"] = 0
        return public


class WorkbenchService:
    def __init__(self, templates: Any, rules: Any, engine_factory: Callable[[], Any], storage_dir: Path, activity_template_fingerprint: str | None = None, scenarios: Any = None, default_engine_mode: str = "cold_com", warm_timeout_seconds: float = 60.0, warm_worker_factory: Any = WarmExcelWorker):
        self.templates, self.rules, self.engine_factory, self.storage_dir = templates, rules, engine_factory, Path(storage_dir)
        self.activity_template_fingerprint = activity_template_fingerprint
        if default_engine_mode not in ("cold_com", "warm_com"):
            raise ValueError("engine mode 必须是 cold_com 或 warm_com")
        self.default_engine_mode = default_engine_mode
        self.warm_timeout_seconds = warm_timeout_seconds
        self.warm_worker_factory = warm_worker_factory
        self._warm_worker: WarmExcelWorker | None = None
        self._warm_lock = threading.Lock()
        self.calculation_tasks = CalculationTaskManager()
        self.scenarios = scenarios or ScenarioStore(":memory:")
        self.reverse_audit: list[dict[str, Any]] = []
        self.exports = ExportService(self.storage_dir / "exports")
        self.display_defaults_path = self.storage_dir / "display-defaults.json"

    def display_defaults(self) -> dict[str, list[str]]:
        if not self.display_defaults_path.exists():
            return {"inputs": [], "outputs": []}
        try:
            payload = json.loads(self.display_defaults_path.read_text(encoding="utf-8"))
            return {"inputs": list(payload.get("inputs") or []), "outputs": list(payload.get("outputs") or [])}
        except (OSError, ValueError, TypeError):
            return {"inputs": [], "outputs": []}

    def save_display_defaults(self, payload: dict[str, Any]) -> dict[str, list[str]]:
        defaults = {
            "inputs": list(dict.fromkeys(str(item) for item in payload.get("inputs") or [])),
            "outputs": list(dict.fromkeys(str(item) for item in payload.get("outputs") or [])),
        }
        self.display_defaults_path.parent.mkdir(parents=True, exist_ok=True)
        self.display_defaults_path.write_text(json.dumps(defaults, ensure_ascii=False, indent=2), encoding="utf-8")
        return defaults

    def export(self, kind: str, payload: dict[str, Any], *, actor: str = "local-user") -> dict[str, Any]:
        subject = payload.get("scenario_id") or payload.get("comparison_id") or payload.get("scenario_draft", {}).get("scenario_id") or f"{kind}:current"
        try:
            exported = self.exports.create(kind, payload)
            self.scenarios.audit(str(subject), "export_succeeded", actor=actor, after={"kind": kind, **exported}, detail=exported["file_name"])
            return exported
        except Exception as exc:
            self.scenarios.audit(str(subject), "export_failed", actor=actor, after={"kind": kind}, detail=str(exc))
            raise

    def get_export(self, file_id: str) -> Path:
        return self.exports.resolve(file_id)

    def start_calculation(self, template_version_id: int, adjustments: list[dict[str, Any]], *, engine_mode: str | None = None) -> dict[str, Any]:
        template = self.templates.get_indicator_catalog(template_version_id)
        if not template:
            raise ValueError("模板版本不存在")
        if self.activity_template_fingerprint and template["template_fingerprint"].casefold() != self.activity_template_fingerprint.casefold():
            raise ValueError("历史模板仅供追溯，不能发起新测算")
        mode = self._engine_mode(engine_mode)
        return self.calculation_tasks.start(self, template_version_id, adjustments, runner=lambda version, payload, **kwargs: self.calculate(version, payload, engine_mode=mode, **kwargs), engine_mode=mode)

    def get_calculation(self, task_id: str) -> dict[str, Any]:
        return self.calculation_tasks.snapshot(task_id)

    def cancel_calculation(self, task_id: str) -> dict[str, Any]:
        return self.calculation_tasks.cancel(task_id)

    def start_comparison(self, request: dict[str, Any], *, actor: str = "local-user") -> dict[str, Any]:
        scenario_ids = list(dict.fromkeys(request.get("scenario_ids") or []))
        if len(scenario_ids) < 2:
            raise ValueError("至少选择两个已保存场景")
        records = [self._get_scenario_record(scenario_id) for scenario_id in scenario_ids]
        baseline_id = request.get("baseline_scenario_id") or scenario_ids[0]
        if baseline_id not in scenario_ids:
            raise ValueError("基准场景必须包含在所选场景中")
        comparison_id = str(uuid.uuid4())
        payload = {**request, "engine_mode": self._engine_mode(request.get("engine_mode")), "scenario_ids": scenario_ids, "baseline_scenario_id": baseline_id, "comparison_id": comparison_id}
        self.scenarios.audit(f"comparison:{comparison_id}", "comparison_started", actor=actor, after={"scenario_ids": scenario_ids, "baseline_scenario_id": baseline_id, "force_refresh": bool(request.get("force_refresh"))})
        task = self.calculation_tasks.start(
            self, records[0]["template_version_id"], payload,
            runner=lambda _version, comparison, **kwargs: self.compare_scenarios(comparison, actor=actor, **kwargs),
            on_complete=lambda completed: self._comparison_done(comparison_id, completed, actor),
            engine_mode=payload["engine_mode"],
        )
        return task

    def _comparison_done(self, comparison_id: str, task: dict[str, Any], actor: str) -> None:
        operation = "comparison_completed" if task["status"] == "succeeded" else "comparison_failed"
        result = task.get("result") or {}
        self.scenarios.audit(
            f"comparison:{comparison_id}", operation, actor=actor,
            after={"status": task["status"], "summary": result.get("summary")},
            detail=task.get("error") or result.get("trust", {}).get("reason") or task["status"],
        )

    def compare_scenarios(self, request: dict[str, Any], *, cancel_token: Any = None, progress: Any = None, actor: str = "local-user") -> dict[str, Any]:
        scenario_ids = request["scenario_ids"]
        baseline_id = request["baseline_scenario_id"]
        force_refresh = bool(request.get("force_refresh"))
        rows: list[dict[str, Any]] = []
        statuses = {scenario_id: {"status": "queued", "reason": None} for scenario_id in scenario_ids}

        for index, scenario_id in enumerate(scenario_ids, 1):
            if cancel_token is not None and cancel_token.is_set():
                raise CalculationCancelled("场景对比已取消")
            record = self._get_scenario_record(scenario_id)
            view = self._scenario_view(record)
            statuses[scenario_id] = {"status": "running", "reason": None}
            if progress:
                progress(f"comparison_scenario_{index}", {"completed": index - 1, "total": len(scenario_ids), "current_scenario": record["name"], "scenarios": statuses})
            snapshot = record.get("calculation_result_snapshot")
            source = "snapshot"
            calculation_details = None
            validation_state = record.get("validation_state")
            reason = None
            if force_refresh or validation_state != "valid" or not snapshot:
                if view["read_only"]:
                    snapshot = None
                    reason = "历史只读场景没有可用 valid 快照，不能重算"
                else:
                    source = "recalculated"
                    try:
                        result = self.calculate(record["template_version_id"], self._scenario_adjustments(record), cancel_token=cancel_token, engine_mode=request.get("engine_mode"))
                        validation_state = result.get("trust", {}).get("status")
                        calculation_details = result.get("calculation_details")
                        draft = result.get("scenario_draft") or {}
                        snapshot = draft.get("calculation_result_snapshot") if validation_state == "valid" else None
                        reason = None if validation_state == "valid" else result.get("trust", {}).get("reason") or validation_state
                    except CalculationCancelled:
                        raise
                    except Exception as exc:
                        snapshot, validation_state, reason = None, "calculation_failed", str(exc)
            status = "succeeded" if validation_state == "valid" and snapshot else validation_state or "calculation_failed"
            statuses[scenario_id] = {"status": status, "reason": reason}
            rows.append({
                "scenario_id": scenario_id, "name": record["name"], "scenario_type": record["scenario_type"],
                "template_version_id": record["template_version_id"], "template_fingerprint": record["template_fingerprint"],
                "rule_publication_id": record["rule_publication_id"], "validation_state": validation_state,
                "read_only": view["read_only"], "source": source, "status": status, "failure_reason": reason,
                "calculation_result_snapshot": snapshot, "calculation_details": calculation_details,
            })
            if progress:
                progress(f"comparison_scenario_{index}", {"completed": index, "total": len(scenario_ids), "current_scenario": record["name"], "scenarios": statuses})

        baseline = next(row for row in rows if row["scenario_id"] == baseline_id)
        baseline_snapshot = baseline.get("calculation_result_snapshot") or {}
        cards = self._comparison_cards(rows, baseline_snapshot)
        details = self._comparison_details(rows, baseline_snapshot)
        valid_count = sum(row["status"] == "succeeded" for row in rows)
        summary = {"total": len(rows), "valid": valid_count, "failed": len(rows) - valid_count}
        refreshed = [row["calculation_details"] for row in rows if row.get("calculation_details")]
        worker_ids = list(dict.fromkeys(item.get("worker_id") for item in refreshed if item.get("worker_id")))
        fallback_reasons = [item["fallback_reason"] for item in refreshed if item.get("fallback_reason")]
        return {
            "comparison_id": request["comparison_id"], "baseline_scenario_id": baseline_id, "scenarios": rows,
            "core_results": cards, "details": details, "summary": summary,
            "trust": {"status": "valid" if valid_count else "calculation_failed", "reason": f"对比完成：{valid_count}/{len(rows)} 个场景结果有效"},
            "calculation_details": {
                "stage": "comparison_completed", "scenario_count": len(rows), "completed_scenarios": len(rows),
                "engine_mode_requested": request.get("engine_mode"),
                "engine_mode": refreshed[-1].get("engine_mode") if refreshed else request.get("engine_mode"),
                "worker_id": worker_ids[0] if len(worker_ids) == 1 else None,
                "worker_ids": worker_ids,
                "queue_wait_ms": round(sum(item.get("queue_wait_ms") or 0 for item in refreshed), 2),
                "stage_timings": {f"scenario_{index + 1}": item.get("stage_timings") or {} for index, item in enumerate(refreshed)},
                "fallback_reason": "; ".join(dict.fromkeys(fallback_reasons)) or None,
                "cancel_status": "not_requested", "finished_at": _utc_now(),
            },
            "task_status": "succeeded",
        }

    def _scenario_adjustments(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        template = self.templates.get_indicator_catalog(record["template_version_id"])
        published_rules = self.rules.get_active_publication_rules(record["template_version_id"])
        if not template or not published_rules:
            raise ValueError("活动模板没有活动规则集，无法重算场景")
        rules_by_name = {item["display_name"]: item for item in published_rules}
        indicators = {self._indicator_id(item): item for item in template["indicator_catalog"]}
        adjustments = []
        for indicator_id, values in record["input_adjustments"].items():
            indicator = indicators.get(indicator_id)
            rule = rules_by_name.get(indicator["display_name"]) if indicator else None
            if not rule:
                raise ValueError(f"场景指标 {indicator_id} 在当前活动规则集中不存在，无法重算")
            adjustments.append({"rule_id": rule["rule_id"], "indicator_id": indicator_id, "values": values})
        return adjustments

    @staticmethod
    def _comparison_cards(rows: list[dict[str, Any]], baseline: dict[str, Any]) -> list[dict[str, Any]]:
        cards = []
        snapshots = [row["calculation_result_snapshot"] or {} for row in rows]
        names = list(dict.fromkeys(name for snapshot in snapshots for name in snapshot))
        for label, aliases in CORE_ALIASES.items():
            metric = next((name for name in names if any(alias.casefold() in name.casefold() for alias in aliases)), None)
            if not metric:
                continue
            values = []
            for row, snapshot in zip(rows, snapshots):
                yearly = snapshot.get(metric) if row["status"] == "succeeded" else None
                values.append({"scenario_id": row["scenario_id"], "name": row["name"], "values": yearly, "differences": WorkbenchService._year_differences(yearly, baseline.get(metric))})
            cards.append({"name": label, "source_name": metric, "scenarios": values})
        return cards

    def _comparison_details(self, rows: list[dict[str, Any]], baseline: dict[str, Any]) -> list[dict[str, Any]]:
        groups: dict[str, str] = {}
        for row in rows:
            template = self.templates.get_indicator_catalog(row["template_version_id"])
            if template:
                groups.update({item["display_name"]: item.get("group") or "未分组" for item in template["indicator_catalog"]})
        names = sorted({name for row in rows for name in (row["calculation_result_snapshot"] or {})})
        return [{
            "name": name, "group": groups.get(name, "未分组"),
            "scenarios": [{"scenario_id": row["scenario_id"], "name": row["name"], "values": (row["calculation_result_snapshot"] or {}).get(name) if row["status"] == "succeeded" else None, "differences": self._year_differences((row["calculation_result_snapshot"] or {}).get(name), baseline.get(name))} for row in rows],
        } for name in names]

    @staticmethod
    def _year_differences(values: dict[str, Any] | None, baseline: dict[str, Any] | None) -> dict[str, Any] | None:
        if not values or not baseline:
            return None
        return {year: value - baseline[year] if isinstance(value, (int, float)) and isinstance(baseline.get(year), (int, float)) else None for year, value in values.items()}

    def start_reverse_calculation(self, template_version_id: int, request: dict[str, Any], *, actor: str = "local-user") -> dict[str, Any]:
        template = self.templates.get_indicator_catalog(template_version_id)
        if not template:
            raise ValueError("模板版本不存在")
        if self.activity_template_fingerprint and template["template_fingerprint"].casefold() != self.activity_template_fingerprint.casefold():
            raise ValueError("历史模板仅供追溯，不能发起反向测算")
        publication = self.rules.get_active_publication(template_version_id) if hasattr(self.rules, "get_active_publication") else None
        if not publication:
            raise ValueError("活动模板没有活动规则集，无法反向测算")
        payload = {**request, "engine_mode": self._engine_mode(request.get("engine_mode"))}
        return self.calculation_tasks.start(self, template_version_id, payload, runner=lambda version, body, **kwargs: self.reverse_calculate(version, body, actor=actor, **kwargs), engine_mode=payload["engine_mode"])

    def reverse_calculate(self, template_version_id: int, request: dict[str, Any], *, cancel_token: Any = None, progress: Any = None, actor: str = "local-user") -> dict[str, Any]:
        if request.get("variables"):
            return self._reverse_calculate_v2(template_version_id, request, cancel_token=cancel_token, progress=progress, actor=actor)
        template = self.templates.get_indicator_catalog(template_version_id)
        published = self.rules.get_active_publication_rules(template_version_id)
        publication = self.rules.get_active_publication(template_version_id) if hasattr(self.rules, "get_active_publication") else None
        variable = request.get("variable") or {}
        variable_rule = next((item for item in published if item["rule_id"] == variable.get("rule_id")), None)
        if not variable_rule or variable_rule.get("confirmation_status") != "confirmed" or variable_rule.get("configuration_pending"):
            raise ValueError("变量指标规则未确认或配置未完成")
        catalog = template["indicator_catalog"]
        indicators = {self._indicator_id(item): item for item in catalog}
        variable_indicator = indicators.get(variable.get("indicator_id"))
        if not variable_indicator or variable_indicator.get("classification") != "input" or variable_indicator["display_name"] != variable_rule["display_name"]:
            raise ValueError("变量指标必须是活动规则集中的 confirmed 输入指标")
        year = str(variable.get("year") or "2030")
        allowed = variable_rule.get("allowed_range") or []
        lower = float(variable.get("lower", allowed[0] if allowed else variable_indicator["year_values"][year] * .5))
        upper = float(variable.get("upper", allowed[1] if allowed else variable_indicator["year_values"][year] * 1.5))
        initial = float(variable.get("initial", variable_indicator["year_values"][year]))
        if not lower <= initial <= upper:
            raise ValueError("变量初始值必须位于搜索范围内")
        constraints = [ReverseConstraint(
            indicator_id=str(item.get("indicator_id") or ""), indicator_name=str(item.get("indicator_name") or ""),
            year=str(item.get("year") or "2030"), kind=str(item.get("kind") or "target"), value=float(item["value"]),
            enabled=bool(item.get("enabled", True)), hard=bool(item.get("hard", True)), tolerance=float(item.get("tolerance") or 0),
            indicator_type=str(item.get("indicator_type") or "output"),
        ) for item in request.get("constraints", [])]
        if not any(item.enabled for item in constraints):
            raise ValueError("至少启用一个约束")
        base_adjustments = list(request.get("adjustments") or [])
        search_timings: dict[str, Any] = {}

        def evaluate(value: float, search_index: int) -> dict[str, Any]:
            if cancel_token is not None and cancel_token.is_set():
                raise CalculationCancelled("反向测算已取消")
            if progress:
                progress(f"reverse_search_{search_index}", {**search_timings, "search_count": search_index})
            adjustments = [item for item in base_adjustments if item.get("indicator_id") != variable["indicator_id"]]
            adjustments.append({"rule_id": variable_rule["rule_id"], "indicator_id": variable["indicator_id"], "values": {year: value}})
            forward = self.calculate(template_version_id, adjustments, cancel_token=cancel_token, engine_mode=request.get("engine_mode"))
            status = forward["trust"]["status"]
            if status != "valid":
                reason = forward["trust"].get("reason") or status
                if status == "cycle_not_converged":
                    raise RuntimeError(f"循环未收敛: {reason}")
                raise RuntimeError(f"正向计算失败: {reason}")
            outputs = forward["scenario_draft"]["calculation_result_snapshot"]
            edited = forward["scenario_draft"]["input_adjustments"]
            def actual(constraint: ReverseConstraint) -> float:
                if constraint.indicator_type == "input":
                    indicator = indicators.get(constraint.indicator_id)
                    values = {**(indicator["year_values"] if indicator else {}), **edited.get(constraint.indicator_id, {})}
                else:
                    values = outputs.get(constraint.indicator_name)
                if not values or constraint.year not in values:
                    raise ValueError(f"约束指标 {constraint.indicator_name or constraint.indicator_id} 不存在或年度无效")
                return values[constraint.year]
            rows, hard_violation, soft_deviation = evaluate_constraints(constraints, actual)
            return {"constraints": rows, "hard_violation": hard_violation, "soft_deviation": soft_deviation, "forward_result": forward}

        result = search_single_variable(lower=lower, upper=upper, evaluate=evaluate, max_evaluations=int(request.get("max_evaluations") or 25), initial=initial)
        forward = result.pop("forward_result")
        feasible = result["feasible"]
        scenario_draft = forward["scenario_draft"] if feasible else None
        if scenario_draft:
            scenario_draft = {**scenario_draft, "scenario_type": "reverse_result"}
        status = "valid" if feasible else "reverse_no_feasible"
        reason = "找到满足全部硬约束的单变量可行解" if feasible else "在变量允许范围和搜索次数内未找到满足全部硬约束的解"
        audit = {"operation": "reverse_calculation", "actor": actor, "occurred_at": _utc_now(), "template_version_id": template_version_id, "rule_publication_id": publication.get("publication_id") if publication else None, "variable": {**variable, "required_value": result["variable_value"]}, "constraints": [item.__dict__ for item in constraints], "search_count": result["search_count"], "result_status": status}
        self.reverse_audit.append(audit)
        self.scenarios.audit(f"reverse:{forward['calculation_details']['calculation_id']}", "reverse_calculation", actor=actor, after=audit, detail=f"status={status}; searches={result['search_count']}")
        return {**result, "variable": {**variable, "indicator_name": variable_rule["display_name"], "required_value": result["variable_value"], "adjustment": result["variable_value"] - float(variable_indicator["year_values"][year])}, "trust": self._trust(status, template, forward["trust"].get("iterations", 0), forward["trust"].get("final_difference"), forward["trust"].get("final_differences", {}), reason, publication), "calculation_details": {**forward["calculation_details"], "stage": "reverse_completed", "search_count": result["search_count"], "audit": audit}, "scenario_draft": scenario_draft, "task_status": "succeeded"}

    def _reverse_calculate_v2(self, template_version_id: int, request: dict[str, Any], *, cancel_token: Any = None, progress: Any = None, actor: str = "local-user") -> dict[str, Any]:
        template = self.templates.get_indicator_catalog(template_version_id)
        published = self.rules.get_active_publication_rules(template_version_id)
        publication = self.rules.get_active_publication(template_version_id) if hasattr(self.rules, "get_active_publication") else None
        indicators = {self._indicator_id(item): item for item in template["indicator_catalog"]}
        rules = {item["rule_id"]: item for item in published}
        raw_variables = request.get("variables") or []
        if len(raw_variables) < 2:
            raise ValueError("v2 至少需要两个可调变量；单变量请使用 v1")
        if len({item.get("indicator_id") for item in raw_variables}) != len(raw_variables):
            raise ValueError("可调变量不能重复")
        base_adjustments = list(request.get("adjustments") or [])
        base_by_indicator = {item.get("indicator_id"): item for item in base_adjustments}
        variables = []
        for order, item in enumerate(raw_variables):
            priority = int(item.get("priority", order + 1))
            if priority < 1:
                raise ValueError("变量优先级必须大于等于 1")
            rule = rules.get(item.get("rule_id"))
            indicator = indicators.get(item.get("indicator_id"))
            if not rule or rule.get("confirmation_status") != "confirmed" or rule.get("configuration_pending"):
                raise ValueError("变量指标规则未确认或配置未完成")
            if not indicator or indicator.get("classification") != "input" or indicator["display_name"] != rule["display_name"]:
                raise ValueError("变量指标必须是活动规则集中的 confirmed 输入指标")
            year = str(item.get("year") or "2030")
            if year not in indicator["year_values"]:
                raise ValueError("变量年度无效")
            existing = (base_by_indicator.get(item["indicator_id"]) or {}).get("values", {})
            baseline = float(item.get("baseline", existing.get(year, indicator["year_values"][year])))
            initial = float(item.get("initial", baseline))
            baseline_values = {**indicator["year_values"], **{str(key): float(value) for key, value in existing.items()}}
            allowed = rule.get("allowed_range") or []
            lower = float(item.get("lower", allowed[0] if allowed else baseline * .5))
            upper = float(item.get("upper", allowed[1] if allowed else baseline * 1.5))
            if not lower <= initial <= upper:
                raise ValueError(f"{rule['display_name']} 初始值必须位于搜索范围内")
            if allowed and (lower < float(allowed[0]) or upper > float(allowed[1])):
                raise ValueError(f"{rule['display_name']} 搜索范围超出规则允许范围")
            step = item.get("step", rule.get("minimum_step"))
            variables.append({
                "key": item["indicator_id"], "order": order, "rule": rule, "indicator": indicator, "baseline_values": baseline_values,
                "year": year, "priority": priority, "baseline": baseline, "initial": initial,
                "lower": lower, "upper": upper, "step": float(step) if step not in (None, "") else None,
                "linkage_strategy": str(item.get("linkage_strategy") or rule.get("linkage_strategy") or "independent"),
                "candidates": variable_candidates(lower=lower, upper=upper, baseline=initial, step=float(step) if step not in (None, "") else None, candidates=item.get("candidates")),
            })
        constraints = [ReverseConstraint(
            indicator_id=str(item.get("indicator_id") or ""), indicator_name=str(item.get("indicator_name") or ""),
            year=str(item.get("year") or "2030"), kind=str(item.get("kind") or "target"), value=float(item["value"]),
            enabled=bool(item.get("enabled", True)), hard=bool(item.get("hard", True)), tolerance=float(item.get("tolerance") or 0),
            indicator_type=str(item.get("indicator_type") or "output"),
        ) for item in request.get("constraints", [])]
        if not any(item.enabled for item in constraints):
            raise ValueError("至少启用一个约束")
        search_timings: dict[str, Any] = {}

        def evaluate(values: dict[str, float], search_index: int) -> dict[str, Any]:
            if cancel_token is not None and cancel_token.is_set():
                raise CalculationCancelled("反向测算已取消")
            if progress:
                progress(f"reverse_v2_search_{search_index}", {**search_timings, "search_count": search_index, "max_evaluations": max_evaluations})
            variable_ids = {item["key"] for item in variables}
            adjustments = [item for item in base_adjustments if item.get("indicator_id") not in variable_ids]
            for item in variables:
                linked = apply_linkage(item["baseline_values"], int(item["year"]), values[item["key"]], item["linkage_strategy"])
                adjustments.append({"rule_id": item["rule"]["rule_id"], "indicator_id": item["key"], "values": linked})
            forward = self.calculate(template_version_id, adjustments, cancel_token=cancel_token, engine_mode=request.get("engine_mode"))
            status = forward["trust"]["status"]
            if status != "valid":
                reason = forward["trust"].get("reason") or status
                raise RuntimeError(("循环未收敛" if status == "cycle_not_converged" else "正向计算失败") + f": {reason}")
            outputs = forward["scenario_draft"]["calculation_result_snapshot"]
            edited = forward["scenario_draft"]["input_adjustments"]

            def actual(constraint: ReverseConstraint) -> float:
                if constraint.indicator_type == "input":
                    indicator = indicators.get(constraint.indicator_id)
                    value_map = {**(indicator["year_values"] if indicator else {}), **edited.get(constraint.indicator_id, {})}
                else:
                    value_map = outputs.get(constraint.indicator_name)
                if not value_map or constraint.year not in value_map:
                    raise ValueError(f"约束指标 {constraint.indicator_name or constraint.indicator_id} 不存在或年度无效")
                return value_map[constraint.year]

            rows, hard_violation, soft_deviation = evaluate_constraints(constraints, actual)
            return {"constraints": rows, "hard_violation": hard_violation, "soft_deviation": soft_deviation, "forward_result": forward}

        max_evaluations = int(request.get("max_evaluations") or 15)
        result = search_priority_variables(variables=variables, evaluate=evaluate, max_evaluations=max_evaluations)
        forward = result.pop("forward_result")
        suggestions = []
        for item in sorted(variables, key=lambda variable: (variable["priority"], variable["order"])):
            value = result["variable_values"][item["key"]]
            suggestions.append({
                "rule_id": item["rule"]["rule_id"], "indicator_id": item["key"], "indicator_name": item["rule"]["display_name"],
                "year": item["year"], "priority": item["priority"], "linkage_strategy": item["linkage_strategy"],
                "baseline_value": item["baseline"], "suggested_value": value, "required_value": value,
                "adjustment": value - item["baseline"], "lower": item["lower"], "upper": item["upper"],
                "hit_boundary": abs(value - item["lower"]) <= 1e-12 or abs(value - item["upper"]) <= 1e-12,
            })
        feasible = result["feasible"]
        scenario_draft = {**forward["scenario_draft"], "scenario_type": "reverse_result"} if feasible else None
        status = "valid" if feasible else "reverse_no_feasible"
        no_feasible_reason = None if feasible else "在变量允许范围、候选点和最大测算次数内未找到满足全部硬约束的解"
        reason = "按优先级找到满足全部硬约束的多输入可行解" if feasible else no_feasible_reason
        audit = {"operation": "reverse_calculation_v2", "actor": actor, "occurred_at": _utc_now(), "template_version_id": template_version_id, "rule_publication_id": publication.get("publication_id") if publication else None, "variables": suggestions, "constraints": result["constraints"], "adjustment_path": result["adjustment_path"], "search_count": result["search_count"], "max_evaluations": max_evaluations, "result_status": status}
        self.reverse_audit.append(audit)
        self.scenarios.audit(f"reverse:{forward['calculation_details']['calculation_id']}", "reverse_calculation_v2", actor=actor, after=audit, detail=f"status={status}; searches={result['search_count']}/{max_evaluations}")
        path_log = [f"优先级调整 {item['order']}：{next(variable['indicator_name'] for variable in suggestions if variable['indicator_id'] == item['key'])} {item['from_value']} → {item['to_value']}" for item in result["adjustment_path"]]
        return {
            **result, "version": 2, "variables": suggestions, "variable": suggestions[0], "no_feasible_reason": no_feasible_reason,
            "trust": self._trust(status, template, forward["trust"].get("iterations", 0), forward["trust"].get("final_difference"), forward["trust"].get("final_differences", {}), reason, publication),
            "calculation_details": {**forward["calculation_details"], "stage": "reverse_v2_completed", "search_count": result["search_count"], "max_evaluations": max_evaluations, "log": [*(forward["calculation_details"].get("log") or []), *path_log, f"优先级搜索共执行 {result['search_count']}/{max_evaluations} 次正向测算"], "audit": audit},
            "scenario_draft": scenario_draft, "task_status": "succeeded",
        }

    def _scenario_view(self, record: dict[str, Any]) -> dict[str, Any]:
        read_only = bool(self.activity_template_fingerprint) and record["template_fingerprint"].casefold() != self.activity_template_fingerprint.casefold()
        return {**record, "read_only": read_only}

    def _get_scenario_record(self, scenario_id: str) -> dict[str, Any]:
        try:
            return self.scenarios.get(scenario_id)
        except KeyError:
            raise ValueError("场景不存在") from None

    def _require_editable_scenario(self, scenario_id: str) -> dict[str, Any]:
        record = self._get_scenario_record(scenario_id)
        if self._scenario_view(record)["read_only"]:
            raise ValueError("历史模板场景只读保留，不能编辑、重算或迁移")
        return record

    def list_scenarios(self) -> dict[str, Any]:
        return {"scenario_types": list(SCENARIO_TYPES), "scenarios": [self._scenario_view(record) for record in self.scenarios.list()]}

    def get_scenario(self, scenario_id: str) -> dict[str, Any]:
        return self._scenario_view(self._get_scenario_record(scenario_id))

    def save_scenario(self, payload: dict[str, Any], *, actor: str = "local-user") -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("场景名称不能为空")
        template_version_id = int(payload["template_version_id"])
        template = self.templates.get_indicator_catalog(template_version_id)
        if not template:
            raise ValueError("模板版本不存在")
        if self.activity_template_fingerprint and template["template_fingerprint"].casefold() != self.activity_template_fingerprint.casefold():
            raise ValueError("历史模板仅供追溯，不能保存新场景")
        record = self.scenarios.create(
            name=name, scenario_type=str(payload.get("scenario_type") or "custom"),
            template_version_id=template_version_id, template_fingerprint=template["template_fingerprint"],
            rule_publication_id=payload.get("rule_publication_id"),
            input_adjustments=payload.get("input_adjustments") or {},
            calculation_result_snapshot=payload.get("calculation_result_snapshot"),
            validation_state=payload.get("validation_state"),
        )
        self.scenarios.audit(record["scenario_id"], "scenario_created", actor=actor, after=record)
        return self._scenario_view(record)

    def copy_scenario(self, scenario_id: str, payload: dict[str, Any], *, actor: str = "local-user") -> dict[str, Any]:
        source = self._get_scenario_record(scenario_id)
        record = self.scenarios.create(
            name=str(payload.get("name") or "").strip() or f"{source['name']} 副本", scenario_type=source["scenario_type"],
            template_version_id=source["template_version_id"], template_fingerprint=source["template_fingerprint"],
            rule_publication_id=source["rule_publication_id"], input_adjustments=source["input_adjustments"],
            calculation_result_snapshot=source["calculation_result_snapshot"], validation_state=source["validation_state"],
        )
        self.scenarios.audit(record["scenario_id"], "scenario_copied", actor=actor, after=record, detail=f"复制自 {scenario_id}")
        return self._scenario_view(record)

    def rename_scenario(self, scenario_id: str, payload: dict[str, Any], *, actor: str = "local-user") -> dict[str, Any]:
        record = self._require_editable_scenario(scenario_id)
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("场景名称不能为空")
        updated = self.scenarios.rename(scenario_id, name)
        self.scenarios.audit(scenario_id, "scenario_renamed", actor=actor, before=record, after=updated)
        return self._scenario_view(updated)

    def delete_scenario(self, scenario_id: str, *, actor: str = "local-user") -> dict[str, Any]:
        record = self._require_editable_scenario(scenario_id)
        self.scenarios.audit(scenario_id, "scenario_deleted", actor=actor, before=record)
        self.scenarios.delete(scenario_id)
        return {"deleted": scenario_id}

    def recalculate_scenario(self, scenario_id: str, *, actor: str = "local-user") -> dict[str, Any]:
        record = self._require_editable_scenario(scenario_id)
        adjustments = self._scenario_adjustments(record)
        self.scenarios.audit(scenario_id, "scenario_recalculate_started", actor=actor, detail=f"{len(adjustments)} 项调整")
        return self.calculation_tasks.start(self, record["template_version_id"], adjustments, on_complete=lambda task: self._scenario_recalc_done(record, task, actor))

    def _scenario_recalc_done(self, record: dict[str, Any], task: dict[str, Any], actor: str) -> None:
        scenario_id = record["scenario_id"]
        if task["status"] in ("succeeded", "cycle_not_converged") and task.get("result"):
            draft = task["result"].get("scenario_draft", {})
            self.scenarios.update_result(
                scenario_id,
                calculation_result_snapshot=draft.get("calculation_result_snapshot"),
                validation_state=draft.get("validation_state"),
                rule_publication_id=draft.get("rule_publication_id"),
            )
            self.scenarios.audit(scenario_id, "scenario_recalculated", actor=actor, detail=f"validation_state={draft.get('validation_state')}")
        else:
            self.scenarios.audit(scenario_id, "scenario_recalculate_failed", actor=actor, detail=task.get("error") or task["status"])

    def initialize(self, template_version_id: int | None = None) -> dict[str, Any]:
        versions = self.templates.list_template_versions()
        if not versions:
            raise RuntimeError("没有可用模板，请先导入 Excel 模板")
        template = self._activity_template(versions)
        if template_version_id is not None and template["template_version_id"] != template_version_id:
            raise ValueError("历史模板仅供追溯，不能作为可编辑工作区")
        catalog = template["indicator_catalog"]
        rules = self.rules.get_active_publication_rules(template["template_version_id"])
        publication = self.rules.get_active_publication(template["template_version_id"]) if hasattr(self.rules, "get_active_publication") else ({"publication_id": "active"} if rules else None)
        by_name = {item["display_name"]: item for item in rules}
        parameters = []
        for indicator in catalog:
            if indicator.get("classification") != "input":
                continue
            current_rule = by_name.get(indicator["display_name"])
            parameters.append({
                "id": self._indicator_id(indicator), "name": indicator["display_name"], "group": indicator["group"],
                "unit": current_rule.get("display_unit") if current_rule else indicator.get("unit"), "row": indicator["row"], "location": indicator.get("cell_address"),
                "baseline": indicator["year_values"], "rule": current_rule, "rule_status": current_rule["confirmation_status"] if current_rule else "unsupported",
            })
        baseline = {item["display_name"]: item["year_values"] for item in catalog if item.get("classification") == "output"}
        result_rows = self._result_rows(catalog, None, None, rules)
        return {
            "template": {"id": template["template_version_id"], "version": template["template_version"], "fingerprint": template["template_fingerprint"], "activity": True, "editable": bool(publication)},
            "rule_set": {"active": bool(publication), "publication_id": publication.get("publication_id") if publication else None, "rule_count": len(rules)},
            "parameters": parameters, "baseline_results": baseline, "core_results": self._core_results(baseline, baseline),
            "details": self._details(catalog, baseline, baseline), "result_rows": result_rows, "display_defaults": self.display_defaults(), "trust": self._trust("pending_rule_confirmation", template, 0, None, {}, "活动模板尚未发布可用规则集" if not publication else "尚未执行测算"),
            "scenario_draft": {"scenario_type": "custom", "template_version_id": template["template_version_id"], "rule_publication_id": publication.get("publication_id") if publication else None, "input_adjustments": {}},
        }

    def _activity_template(self, versions: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.activity_template_fingerprint:
            return versions[-1]
        template = next((item for item in versions if item["template_fingerprint"].casefold() == self.activity_template_fingerprint.casefold()), None)
        if not template:
            raise RuntimeError("活动模板 0717 尚未导入，历史模板不能作为可编辑工作区")
        return template

    def rule_admin(self, template_version_id: int, *, status: str | None = None, search: str = "", group: str | None = None, confidence: str | None = None, diagnostic: str | None = None, configuration: str | None = None) -> dict[str, Any]:
        template = self.templates.get_indicator_catalog(template_version_id)
        if not template:
            raise ValueError("模板版本不存在")
        rules = self.rules.list_latest_rule_summaries(template_version_id) if hasattr(self.rules, "list_latest_rule_summaries") else self._latest_rules(template_version_id)
        counts = {name: sum(rule["confirmation_status"] == name for rule in rules) for name in ("pending_confirmation", "confirmed", "changed", "rejected", "unsupported")}
        counts["total"] = len(rules); counts["configuration_incomplete"] = sum(rule["configuration_pending"] for rule in rules)
        search = search.casefold().strip()
        visible = [rule for rule in rules if (not status or rule["confirmation_status"] == status) and (not search or search in rule["display_name"].casefold()) and (not group or rule["indicator_group"] == group) and (not confidence or rule["confidence"] == confidence) and (not diagnostic or rule.get("discovery_diagnostics", {}).get(diagnostic)) and (not configuration or (configuration == "incomplete") == bool(rule.get("configuration_pending")))]
        return {"template": {"id": template_version_id, "version": template["template_version"], "fingerprint": template["template_fingerprint"]}, "counts": counts, "groups": sorted({rule["indicator_group"] for rule in rules}), "rules": visible}

    def rule_admin_bootstrap(self) -> dict[str, Any]:
        versions = self.templates.list_template_versions()
        if not versions:
            raise RuntimeError("没有可用模板")
        template = self._activity_template(versions)
        return {"template": {"id": template["template_version_id"], "version": template["template_version"], "fingerprint": template["template_fingerprint"]}, "templates": [{"id": item["template_version_id"], "version": item["template_version"], "fingerprint": item["template_fingerprint"]} for item in versions]}

    def update_rule(self, template_version_id: int, rule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.rules.get_rule(rule_id, include_snapshot=False)
        if not current:
            raise ValueError("规则不存在或不是最新版本")
        action, actor = payload.get("action"), "local-admin"
        if action == "reject":
            return self.rules.reject_rule(rule_id, expected_version=int(payload["expected_version"]), reason=str(payload.get("rejection_reason") or ""), actor=actor)
        if action not in {"confirm", "edit"}:
            raise ValueError("不支持的规则操作")
        configuration = {
            "display_unit": payload.get("display_unit", current.get("display_unit")),
            "adjustment_mode": payload.get("adjustment_mode"),
            "minimum_step": self._optional_number(payload.get("minimum_step")),
            "allowed_range": self._allowed_range(payload.get("allowed_range")),
            "linkage_strategy": payload.get("linkage_strategy"),
            "configuration_pending": False,
        }
        if not configuration["adjustment_mode"] or not configuration["linkage_strategy"]:
            raise ValueError("调整模式和五年联动策略不能为空")
        if action == "confirm":
            sources = payload.get("selected_sources") or {}
            if set(sources) != {str(year) for year in YEARS} or any(not item.get("sheet") or not item.get("cell") for item in sources.values()):
                raise ValueError("确认规则必须选择完整五个年度的源单元格")
            template = self.templates.get_indicator_catalog(template_version_id)
            sheet_names = {item["name"] for item in template.get("worksheets", [])}
            for source in sources.values():
                if sheet_names and source["sheet"] not in sheet_names:
                    raise ValueError(f"源工作表不存在: {source['sheet']}")
                if not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*", source["cell"].replace("$", "").upper()):
                    raise ValueError(f"源单元格地址无效: {source['cell']}")
            return self.rules.confirm_and_configure(rule_id, expected_version=int(payload["expected_version"]), selected_sources=sources, configuration=configuration, actor=actor)
        return self.rules.edit_rule(rule_id, expected_version=int(payload["expected_version"]), configuration=configuration, actor=actor)

    def rule_set(self, template_version_id: int) -> dict[str, Any]:
        status = self.rules.rule_set_status(template_version_id)
        template = self.templates.get_indicator_catalog(template_version_id)
        if not template:
            raise ValueError("模板版本不存在")
        summaries = self.rules.list_latest_rule_summaries(template_version_id) if hasattr(self.rules, "list_latest_rule_summaries") else self._latest_rules(template_version_id)
        rule_names = {rule["display_name"] for rule in summaries}
        missing = [item for item in template["indicator_catalog"] if item.get("classification") == "input" and item["display_name"] not in rule_names]
        return {**status, "complete": status["complete"] and not missing, "missing_indicators": missing}

    def activate_rule_set(self, template_version_id: int, *, actor: str) -> dict[str, Any]:
        template = self.templates.get_indicator_catalog(template_version_id)
        if not template:
            raise ValueError("模板版本不存在")
        if not self.rule_set(template_version_id)["complete"]:
            raise ValueError("规则集存在未解决、缺失或配置未完成规则")
        return self.rules.activate_rule_set(template_version_id, template["template_fingerprint"], actor=actor)

    def deactivate_rule_set(self, template_version_id: int, *, actor: str) -> dict[str, Any]:
        return self.rules.deactivate_rule_set(template_version_id, actor=actor)

    def rule_detail(self, template_version_id: int, rule_id: str) -> dict[str, Any]:
        rule = self.rules.get_rule_review(rule_id)
        if not rule:
            raise ValueError("规则不存在或不是最新版本")
        audit = self.rules.list_audit_logs(logical_rule_id=rule["logical_rule_id"])
        history = self.rules.get_rule_history_summaries(rule["logical_rule_id"]) if hasattr(self.rules, "get_rule_history_summaries") else self.rules.get_rule_history(rule["logical_rule_id"])
        version_diff = self.rules.get_version_diff(rule_id) if hasattr(self.rules, "get_version_diff") else {}
        return {"rule": rule, "history": history, "audit": audit, "version_diff": version_diff}

    def rescan_template(self, template_version_id: int, *, actor: str, force: bool = False) -> dict[str, Any]:
        if not force and self.rules.list_latest_rule_summaries(template_version_id):
            return {"template_version_id": template_version_id, "created_versions": 0, "new": 0, "reused": 0, "changed": 0, "unmatched": 0, "skipped": True, "reason": "该模板已有扫描结果，请使用明确的重新扫描命令"}
        if self.engine_factory is ExcelComWorkbookEngine:
            completed = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--rescan-template", str(template_version_id), "--actor", actor],
                cwd=Path(__file__).parent, capture_output=True, text=True, timeout=1800,
            )
            if completed.returncode != 0:
                message = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
                raise RuntimeError(f"Excel 扫描进程异常终止：{message}")
            return json.loads(completed.stdout)
        return self._rescan_template(template_version_id, actor=actor)

    def _rescan_template(self, template_version_id: int, *, actor: str) -> dict[str, Any]:
        template = self.templates.get_indicator_catalog(template_version_id)
        if not template: raise ValueError("模板版本不存在")
        path = self.storage_dir / template["storage_id"]
        engine = self.engine_factory()
        try:
            engine.open_isolated(path)
            graph = build_formula_graph(engine, template["indicator_catalog"], template["worksheet"])
            before = {rule["logical_rule_id"]: rule for rule in self.rules.list_latest_rule_summaries(template_version_id)}
            rules = self.rules.discover_rules(template_version_id, template["template_fingerprint"], template["worksheet"], template["indicator_catalog"], graph, actor)
            counts = {"new": 0, "reused": 0, "changed": 0, "unmatched": 0}
            seen = set()
            for rule in rules:
                seen.add(rule["logical_rule_id"])
                if rule["logical_rule_id"] not in before: counts["new"] += 1
                elif rule["confirmation_status"] == "changed": counts["changed"] += 1
                else: counts["reused"] += 1
            counts["unmatched"] = len(set(before) - seen)
            return {"template_version_id": template_version_id, "created_versions": len(rules), **counts}
        finally: engine.close()

    @staticmethod
    def _optional_number(value: Any) -> float | None:
        return None if value in (None, "") else float(value)

    @staticmethod
    def _allowed_range(value: Any) -> list[float] | None:
        if value in (None, "", []):
            return None
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("允许范围必须包含最小值和最大值")
        allowed = [float(value[0]), float(value[1])]
        if allowed[0] > allowed[1]:
            raise ValueError("允许范围最小值不能大于最大值")
        return allowed

    def _engine_mode(self, mode: str | None) -> str:
        selected = mode or self.default_engine_mode
        if selected not in ("cold_com", "warm_com"):
            raise ValueError("engine mode 必须是 cold_com 或 warm_com")
        return selected

    def _get_warm_worker(self) -> WarmExcelWorker:
        if not self.activity_template_fingerprint:
            raise WarmExcelWorkerError("warm_com requires an activity template SHA-256")
        with self._warm_lock:
            if self._warm_worker is not None and not self._warm_worker.health()["healthy"]:
                self._warm_worker.shutdown()
                self._warm_worker = None
            if self._warm_worker is None:
                self._warm_worker = self.warm_worker_factory(self.activity_template_fingerprint, timeout_seconds=self.warm_timeout_seconds)
            return self._warm_worker

    def warm_worker_health(self) -> dict[str, Any]:
        with self._warm_lock:
            return self._warm_worker.health() if self._warm_worker else {"healthy": False, "worker_id": None, "queue_depth": 0, "error": "not_started"}

    def warm_worker_recheck(self) -> dict[str, Any]:
        try:
            return self._get_warm_worker().health()
        except WarmExcelWorkerError as exc:
            return {"healthy": False, "worker_id": None, "queue_depth": 0, "error": str(exc)}

    def latest_engine_validation(self) -> dict[str, Any]:
        reports = sorted((self.storage_dir.parent.parent / ".scratch" / "perf").glob("ubuntu-engine-validation-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not reports:
            raise ValueError("尚未生成 Ubuntu 引擎验证报告")
        return json.loads(reports[0].read_text(encoding="utf-8"))

    def cleanup_warm_worker(self) -> bool:
        with self._warm_lock:
            return self._warm_worker.cleanup_orphan() if self._warm_worker else False

    def calculate(self, template_version_id: int, adjustments: list[dict[str, Any]], *, cancel_token: Any = None, progress: Any = None, engine_mode: str | None = None) -> dict[str, Any]:
        template = self.templates.get_indicator_catalog(template_version_id)
        if not template:
            raise ValueError("模板版本不存在")
        if self.activity_template_fingerprint and template["template_fingerprint"].casefold() != self.activity_template_fingerprint.casefold():
            raise ValueError("历史模板仅供追溯，不能发起新测算")
        requested_engine_mode = self._engine_mode(engine_mode)
        calculation_id, started_at, started = str(uuid.uuid4()), datetime.now(timezone.utc).isoformat(), time.perf_counter()
        published_rules = self.rules.get_active_publication_rules(template_version_id)
        if not published_rules:
            return {"edited_values": {}, "core_results": [], "details": [], "calculation_details": {"calculation_id": calculation_id, "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(), "duration_ms": round((time.perf_counter() - started) * 1000, 2), "stage": "blocked", "submitted_adjustments": adjustments, "written_source_cells": [], "log": ["活动规则集检查失败：活动模板没有活动发布"]}, "trust": self._trust("pending_rule_confirmation", template, 0, None, {}, "活动模板没有活动规则集")}
        rules = {item["rule_id"]: item for item in published_rules}
        catalog = template["indicator_catalog"]
        indicators = {self._indicator_id(item): item for item in catalog}
        edited_values, requests, written = {}, [], []
        for adjustment in adjustments:
            rule = rules.get(adjustment.get("rule_id"))
            indicator = indicators.get(adjustment.get("indicator_id"))
            if not rule or not indicator or rule["display_name"] != indicator["display_name"]:
                raise ValueError("指标或规则标识无效")
            values = {str(year): float(value) for year, value in adjustment.get("values", {}).items()}
            edited_values[adjustment["indicator_id"]] = values
            if rule["confirmation_status"] != "confirmed" or rule.get("configuration_pending"):
                return {"edited_values": edited_values, "core_results": [], "details": [], "calculation_details": {"calculation_id": calculation_id, "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(), "duration_ms": round((time.perf_counter() - started) * 1000, 2), "stage": "blocked", "submitted_adjustments": adjustments, "written_source_cells": [], "log": [f"规则检查失败：{rule['display_name']} 尚未确认或配置未完成"]}, "trust": self._trust("pending_rule_confirmation", template, 0, None, {}, "所选指标规则尚未确认或配置未完成")}
            allowed = rule.get("allowed_range")
            if allowed:
                for value in values.values():
                    if not allowed[0] <= value <= allowed[1]:
                        raise ValueError(f"{rule['display_name']} 超出允许范围 {allowed[0]}–{allowed[1]}")
            sources = rule["confirmed_source_cells"]
            if isinstance(sources, dict):
                sources = [{"year": year, **source} for year, source in sources.items()]
            sheets = {item["sheet"] for item in sources}
            if len(sheets) != 1:
                raise ValueError("已确认规则必须指向单一源工作表")
            confirmed = ConfirmedInputRule(rule["display_name"], next(iter(sheets)), {int(item["year"]): item["cell"] for item in sources})
            requests.append({"input_rule": confirmed, "values": {int(year): value for year, value in values.items()}})
            written.append({"rule_id": rule["rule_id"], "indicator": rule["display_name"], "source_cells": sources, "values": values})
        path = self.storage_dir / template["storage_id"] if template.get("storage_id") else TEMPLATE_PATH
        fallback_reason = None
        if requested_engine_mode == "warm_com":
            try:
                if progress:
                    progress("warm_queue", {})
                result = self._get_warm_worker().calculate(
                    CalculationRequest(input_adjustments=requests), template_path=path, cancel_token=cancel_token,
                    progress=progress, timeout_seconds=self.warm_timeout_seconds,
                )
            except CalculationCancelled:
                raise
            except WarmExcelWorkerError as exc:
                fallback_reason = str(exc)
                with self._warm_lock:
                    worker, self._warm_worker = self._warm_worker, None
                if worker:
                    worker.shutdown()
                result = run_forward_calculation(self.engine_factory(), CalculationRequest(input_adjustments=requests), template_path=path, cancel_token=cancel_token, progress=progress)
                result.update({"engine_mode": "cold_com", "worker_id": None, "queue_wait_ms": 0.0, "cancel_status": "not_requested"})
        else:
            result = run_forward_calculation(self.engine_factory(), CalculationRequest(input_adjustments=requests), template_path=path, cancel_token=cancel_token, progress=progress)
            result.update({"engine_mode": "cold_com", "worker_id": None, "queue_wait_ms": 0.0, "cancel_status": "not_requested"})
        outputs = result["output_indicators"]
        baseline = result["summary_before"] or {item["display_name"]: item["year_values"] for item in catalog if item.get("classification") == "output"}
        result_rows = self._result_rows(catalog, outputs, baseline, published_rules)
        finished_at = datetime.now(timezone.utc).isoformat()
        publication = self.rules.get_active_publication(template_version_id) if hasattr(self.rules, "get_active_publication") else None
        return {
            "edited_values": edited_values, "core_results": self._core_results(outputs, baseline), "details": self._details(catalog, outputs, baseline), "result_rows": result_rows,
            "calculation_details": {"calculation_id": calculation_id, "started_at": started_at, "finished_at": finished_at, "duration_ms": round((time.perf_counter() - started) * 1000, 2), "stage": "completed" if result["calculation_status"] == "valid" else "failed", "template_version_id": template_version_id, "rule_publication_id": publication.get("publication_id") if publication else None, "engine_mode_requested": requested_engine_mode, "engine_mode": result.get("engine_mode", requested_engine_mode), "worker_id": result.get("worker_id"), "queue_wait_ms": result.get("queue_wait_ms", 0), "cancel_status": result.get("cancel_status", "not_requested"), "fallback_reason": fallback_reason, "submitted_adjustments": adjustments, "written_source_cells": written, "cycle_converged": result["cycle_converged"], "iterations": result["iterations"], "final_differences": result["final_differences"], "stage_timings": result.get("stage_timings", {}), "error": result["error"], "log": [f"计算引擎 {result.get('engine_mode', requested_engine_mode)}" + (f"（warm 回退：{fallback_reason}）" if fallback_reason else ""), f"已校验 {len(adjustments)} 项输入调整", f"已写入 {len(written)} 条活动规则映射", f"循环计算 {result['iterations']} 次", "计算有效" if result["calculation_status"] == "valid" else (result["error"] or result["calculation_status"])]},
            "trust": self._trust(result["calculation_status"], template, result["iterations"], result["final_difference"], result["final_differences"], result["error"], publication),
            "scenario_draft": {"scenario_type": "custom", "template_version_id": template_version_id, "rule_publication_id": publication.get("publication_id") if publication else None, "input_adjustments": edited_values, "calculation_result_snapshot": outputs, "validation_state": result["calculation_status"]},
        }

    def _latest_rules(self, version_id: int) -> list[dict[str, Any]]:
        latest = {}
        for rule in self.rules.list_rules(version_id):
            key = rule.get("logical_rule_id", rule["rule_id"])
            if key not in latest or rule["rule_version"] > latest[key]["rule_version"]:
                latest[key] = rule
        return list(latest.values())

    @staticmethod
    def _indicator_id(item: dict[str, Any]) -> str:
        return f"{item['group']}|{item['display_name']}|{item['row']}"

    def _result_rows(self, catalog: list[dict[str, Any]], outputs: dict[str, Any] | None, baseline: dict[str, Any] | None, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        precision_by_identity = {rule["indicator_key"]: self._display_precision(rule) for rule in rules if rule.get("indicator_key")}
        rows = []
        for item in catalog:
            if item.get("classification") == "input":
                continue
            values = (outputs or {}).get(item["display_name"]) or {
                "2025": item.get("result_values", {}).get("2025"),
                **item["year_values"],
                "five_year_change": item.get("result_values", {}).get("five_year_change"),
                "cagr": item.get("result_values", {}).get("cagr"),
            }
            rows.append({
                "id": self._indicator_id(item), "name": item["display_name"], "group": item["group"],
                "unit": item.get("unit"), "location": item.get("cell_address"),
                "values": values,
                "baseline_values": dict(values) if baseline is None else baseline.get(item["display_name"]),
                "precision": precision_by_identity.get(self._indicator_id(item)),
            })
        return rows

    @staticmethod
    def _display_precision(rule: dict[str, Any]) -> int | None:
        step = rule.get("minimum_step")
        if not isinstance(step, (int, float)) or isinstance(step, bool) or step <= 0:
            return None
        return len(f"{step:.10f}".rstrip("0").rstrip(".").partition(".")[2])

    @staticmethod
    def _core_results(outputs: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
        cards = []
        for label, aliases in CORE_ALIASES.items():
            match = next((name for name in outputs if any(alias.casefold() in name.casefold() for alias in aliases)), None)
            if match:
                values, before = outputs[match], baseline.get(match, {})
                cards.append({"name": label, "source_name": match, "values": values, "changes": {year: (value - before[year]) if isinstance(value, (int, float)) and isinstance(before.get(year), (int, float)) else None for year, value in values.items()}})
        return cards

    @staticmethod
    def _details(catalog: list[dict[str, Any]], outputs: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
        details = []
        for item in catalog:
            values = outputs.get(item["display_name"], item["year_values"])
            before = baseline.get(item["display_name"], item["year_values"])
            details.append({"name": item["display_name"], "group": item["group"], "classification": item["classification"], "unit": item.get("unit"), "location": item.get("cell_address") or f"row:{item['row']}", "values": values, "changes": {year: (value - before[year]) if isinstance(value, (int, float)) and isinstance(before.get(year), (int, float)) else None for year, value in values.items()}})
        return details

    @staticmethod
    def _trust(status: str, template: dict[str, Any], iterations: int, difference: float | None, differences: dict[str, Any], error: str | None, publication: dict[str, Any] | None = None) -> dict[str, Any]:
        reasons = {"valid": "Excel 模型已完成重算并通过循环收敛检查", "pending_rule_confirmation": "存在尚未确认或配置未完成的规则", "cycle_not_converged": "循环逼近达到上限仍未收敛", "engine_difference": "计算引擎结果存在差异", "calculation_failed": "工作簿计算失败"}
        return {"status": status, "reason": error or reasons.get(status, status), "template_version": template["template_version"], "rule_version": publication.get("publication_id") if publication else None, "iterations": iterations, "final_difference": difference, "final_differences": differences, "error": error}


def create_runtime(root: Path) -> WorkbenchService:
    data = root / ".workbench"
    data.mkdir(exist_ok=True)
    template_service = TemplateImportService(data / "templates", data / "catalog.sqlite3", ExcelComWorkbookEngine())
    versions = template_service.list_template_versions()
    if not any(item["template_fingerprint"].casefold() == ACTIVITY_TEMPLATE_FINGERPRINT for item in versions):
        template = template_service.import_template(TEMPLATE_PATH)
        if template["import_status"] != "success": raise RuntimeError(template["error"])
    rule_service = RuleService(data / "rules.sqlite3")
    scenario_store = ScenarioStore(data / "scenarios.sqlite3")
    return WorkbenchService(template_service, rule_service, ExcelComWorkbookEngine, data / "templates", ACTIVITY_TEMPLATE_FINGERPRINT, scenario_store, default_engine_mode=os.environ.get("WORKBENCH_ENGINE_MODE", "cold_com"), warm_timeout_seconds=float(os.environ.get("WORKBENCH_WARM_TIMEOUT_SECONDS", "60")))


def build_handler(service: WorkbenchService, static: Path, admin_token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _is_admin(self) -> bool:
            return f"rule_admin_session={admin_token}" in self.headers.get("Cookie", "")
        def _require_admin(self) -> bool:
            if self._is_admin(): return True
            self._send(403, {"error": "admin authorization required"}); return False
        def _send(self, status: int, payload: Any, content_type="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            try:
                self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/admin/session":
                self._send(200, {"admin": self._is_admin()}); return
            if path == "/api/rule-admin":
                try: self._send(200, service.rule_admin_bootstrap())
                except Exception as exc: self._send(400, {"error": str(exc)})
                return
            if path == "/api/workbench":
                try: self._send(200, service.initialize())
                except Exception as exc: self._send(500, {"error": str(exc)})
                return
            if path == "/api/display-defaults":
                self._send(200, service.display_defaults()); return
            if path.startswith("/api/calculations/"):
                try: self._send(200, service.get_calculation(path.split("/")[3]))
                except ValueError as exc: self._send(404, {"error": str(exc)})
                except Exception as exc: self._send(500, {"error": str(exc)})
                return
            if path == "/api/engine-validation":
                try: self._send(200, service.latest_engine_validation())
                except ValueError as exc: self._send(404, {"error": str(exc)})
                return
            if path == "/api/warm-health":
                try: self._send(200, service.warm_worker_recheck())
                except Exception as exc: self._send(500, {"error": str(exc)})
                return
            if path.startswith("/api/exports/"):
                try:
                    export = service.get_export(path.split("/")[3])
                    self._send(200, export.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                except ValueError as exc: self._send(404, {"error": str(exc)})
                return
            if path == "/api/scenarios":
                try: self._send(200, service.list_scenarios())
                except Exception as exc: self._send(500, {"error": str(exc)})
                return
            if path.startswith("/api/scenarios/"):
                try: self._send(200, service.get_scenario(path.split("/")[3]))
                except ValueError as exc: self._send(404, {"error": str(exc)})
                except Exception as exc: self._send(500, {"error": str(exc)})
                return
            if path == "/api/rules":
                try:
                    query = __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(urlparse(self.path).query)
                    template_version_id = int(query["template_version_id"][0]) if "template_version_id" in query else service.rule_admin_bootstrap()["template"]["id"]
                    self._send(200, service.rule_admin(template_version_id, status=query.get("status", [None])[0], search=query.get("search", [""])[0], group=query.get("group", [None])[0], confidence=query.get("confidence", [None])[0], diagnostic=query.get("diagnostic", [None])[0], configuration=query.get("configuration", [None])[0]))
                except Exception as exc: self._send(400, {"error": str(exc)})
                return
            if path.startswith("/api/rules/") and path.endswith("/chains"):
                try:
                    query = __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(urlparse(self.path).query); rule_id = path.split("/")[3]
                    self._send(200, service.rules.get_formula_chains(rule_id, query["year"][0], offset=int(query.get("offset", [0])[0]), limit=min(int(query.get("limit", [20])[0]), 100)))
                except Exception as exc: self._send(400, {"error": str(exc)})
                return
            if path.startswith("/api/rules/"):
                try:
                    query = __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(urlparse(self.path).query)
                    self._send(200, service.rule_detail(int(query["template_version_id"][0]), path.rsplit("/", 1)[-1]))
                except Exception as exc: self._send(400, {"error": str(exc)})
                return
            if path == "/api/rule-set":
                try:
                    query = __import__("urllib.parse", fromlist=["parse_qs"]).parse_qs(urlparse(self.path).query)
                    self._send(200, service.rule_set(int(query["template_version_id"][0])))
                except Exception as exc: self._send(400, {"error": str(exc)})
                return
            file = static / ("index.html" if path == "/" else path.lstrip("/"))
            if not file.is_file() or static.resolve() not in file.resolve().parents: self._send(404, {"error": "not found"}); return
            self._send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream")
        def do_POST(self):
            path = urlparse(self.path).path
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if path == "/api/admin/login":
                    if not secrets.compare_digest(str(body.get("token") or ""), admin_token): self._send(403, {"error": "invalid admin token"}); return
                    response=json.dumps({"role":"admin"}).encode(); self.send_response(200); self.send_header("Set-Cookie", f"rule_admin_session={admin_token}; HttpOnly; SameSite=Strict; Path=/"); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(response))); self.end_headers(); self.wfile.write(response); return
                if path == "/api/calculate":
                    self._send(200, service.calculate(int(body["template_version_id"]), body.get("adjustments", [])))
                elif path == "/api/calculations":
                    self._send(202, service.start_calculation(int(body["template_version_id"]), body.get("adjustments", []), engine_mode=body.get("engine_mode")))
                elif path == "/api/reverse-calculations":
                    self._send(202, service.start_reverse_calculation(int(body["template_version_id"]), body, actor=body.get("actor", "local-user")))
                elif path == "/api/comparisons":
                    self._send(202, service.start_comparison(body, actor=body.get("actor", "local-user")))
                elif path in ("/api/exports/scenario", "/api/exports/reverse", "/api/exports/comparison"):
                    self._send(201, service.export(path.rsplit("/", 1)[-1], body, actor=body.get("actor", "local-user")))
                elif path.startswith("/api/calculations/") and path.endswith("/cancel"):
                    self._send(200, service.cancel_calculation(path.split("/")[3]))
                elif path == "/api/scenarios":
                    self._send(201, service.save_scenario(body, actor=body.get("actor", "local-user")))
                elif path == "/api/display-defaults":
                    if not self._require_admin(): return
                    self._send(200, service.save_display_defaults(body))
                elif path.startswith("/api/scenarios/") and len(path.split("/")) == 5:
                    scenario_id, action = path.split("/")[3], path.split("/")[4]
                    if action == "copy": self._send(201, service.copy_scenario(scenario_id, body, actor=body.get("actor", "local-user")))
                    elif action == "rename": self._send(200, service.rename_scenario(scenario_id, body, actor=body.get("actor", "local-user")))
                    elif action == "recalculate": self._send(202, service.recalculate_scenario(scenario_id, actor=body.get("actor", "local-user")))
                    else: self._send(404, {"error": "not found"})
                elif not self._require_admin(): return
                elif path == "/api/rules/rescan":
                    self._send(200, service.rescan_template(int(body["template_version_id"]), actor="local-admin", force=bool(body.get("force"))))
                elif path.startswith("/api/rules/"):
                    self._send(200, service.update_rule(int(body["template_version_id"]), path.rsplit("/", 1)[-1], body))
                elif path == "/api/rule-set/activate":
                    self._send(200, service.activate_rule_set(int(body["template_version_id"]), actor=body.get("actor", "admin")))
                elif path == "/api/rule-set/deactivate":
                    self._send(200, service.deactivate_rule_set(int(body["template_version_id"]), actor=body.get("actor", "admin")))
                else: self._send(404, {"error": "not found"})
            except ValueError as exc: self._send(400, {"error": str(exc)})
            except Exception as exc: self._send(500, {"error": str(exc)})
        def do_DELETE(self):
            path = urlparse(self.path).path
            try:
                if path.startswith("/api/scenarios/") and len(path.split("/")) == 4:
                    self._send(200, service.delete_scenario(path.split("/")[3]))
                else: self._send(404, {"error": "not found"})
            except ValueError as exc: self._send(400, {"error": str(exc)})
            except Exception as exc: self._send(500, {"error": str(exc)})
        def log_message(self, format, *args): print(format % args)
    return Handler


def serve(host: str, port: int, root: Path, admin_token: str | None = None) -> None:
    service, static, admin_token = create_runtime(root), root / "web", admin_token or os.environ.get("RULE_ADMIN_TOKEN") or secrets.token_urlsafe(24)
    print(f"管理员令牌：{admin_token}")
    print(f"工作台已启动：http://{host}:{port}")
    ThreadingHTTPServer((host, port), build_handler(service, static, admin_token)).serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8765); parser.add_argument("--admin-token"); parser.add_argument("--rescan-template", type=int); parser.add_argument("--actor", default="local-admin"); parser.add_argument("--force-rescan", action="store_true")
    args = parser.parse_args()
    if args.rescan_template is not None:
        service = create_runtime(Path(__file__).parent)
        try: print(json.dumps(service._rescan_template(args.rescan_template, actor=args.actor), ensure_ascii=False))
        finally: service.templates.close(); service.rules.close()
    else:
        serve(args.host, args.port, Path(__file__).parent, args.admin_token)
