from __future__ import annotations

import shutil
import os
import atexit
import hashlib
import queue
import re
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


TEMPLATE_PATH = Path(__file__).parent / "模版" / "2026-2030年盈利测算表0717-模板.xlsx"


class CalculationCancelled(Exception):
    """取消令牌在安全停止点（阶段边界或循环迭代边界）生效，测算未完成且不产生结果。"""


def _normalize_sheet_name(name: str) -> str:
    return "".join(character for character in name.strip().lower() if character.isalnum())


@dataclass
class CalculationRequest:
    input_adjustment: dict[int, Any] = field(default_factory=dict)
    input_rule: "ConfirmedInputRule | None" = None
    rule_record: dict[str, Any] | None = None
    input_adjustments: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ConfirmedInputRule:
    indicator: str
    source_sheet: str
    year_cells: dict[int, str]
    confirmed: bool = True
    source_sheet_index: int | None = None


class WorkbookEngine(Protocol):
    stage_timings: dict[str, list[float]]
    def engine_info(self) -> dict[str, Any]: ...
    def diagnostics(self) -> dict[str, Any]: ...
    def open_isolated(self, template_path: Path) -> None: ...
    def inspect_workbook(self) -> dict[str, Any]: ...
    def read_indicator_catalog(self) -> dict[str, Any]: ...
    def read_cell_formula_or_value(self, sheet: str, cell: str) -> Any: ...
    def read_summary(self, stage: str = "summary_read") -> dict[str, Any]: ...
    def write_input(self, rule: ConfirmedInputRule, adjustment: dict[int, Any]) -> None: ...
    def recalculate(self, stage: str = "recalculate") -> None: ...
    def copy_cycle_ranges(self) -> None: ...
    def read_cycle_differences(self) -> dict[str, float]: ...
    def close(self) -> None: ...


def section_level(title: str) -> int | None:
    """识别 Excel 总括标题行：一、二、… 编号为 1 级，（一）（二）… 为 2 级，其余不是标题。"""
    if re.match(r"^[一二三四五六七八九十]+、", title):
        return 1
    if re.match(r"^（[一二三四五六七八九十]+）", title):
        return 2
    return None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_forward_calculation(
    engine: WorkbookEngine,
    request: CalculationRequest,
    *,
    template_path: Path = TEMPLATE_PATH,
    tolerance: float = 0.1,
    max_iterations: int = 20,
    read_baseline: bool = True,
    cancel_token: Any = None,
    progress: Any = None,
) -> dict[str, Any]:
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")
    if max_iterations < 1:
        raise ValueError("max_iterations must be at least 1")

    def cancelled() -> bool:
        return cancel_token is not None and cancel_token.is_set()

    def report(stage: str) -> None:
        if progress is not None:
            progress(stage, dict(getattr(engine, "stage_timings", None) or {}))
    rule = request.input_rule
    if request.rule_record:
        record = request.rule_record
        if record.get("confirmation_status") != "confirmed":
            return {
                "input_adjustments": {"indicator": record.get("display_name"), "values": {str(year): value for year, value in request.input_adjustment.items()}},
                "output_indicators": {}, "summary_before": {}, "calculation_status": "pending_rule_confirmation",
                "cycle_converged": False, "iterations": 0, "final_difference": None, "final_differences": {},
                "pending_rules": [{"indicator": record.get("display_name"), "rule_status": record.get("confirmation_status"), "candidate_source_cells": record.get("candidate_source_cells", [])}],
                "error": "input rule requires confirmation",
            }
        sources = record.get("confirmed_source_cells", [])
        if isinstance(sources, dict):
            sources = [{"year": year, **source} for year, source in sources.items()]
        sheets = {item["sheet"] for item in sources}
        if len(sheets) != 1:
            raise ValueError("confirmed rule must use one source sheet")
        rule = ConfirmedInputRule(record["display_name"], next(iter(sheets)), {int(item["year"]): item["cell"] for item in sources})
    result = {
        "input_adjustments": {
            "indicator": rule.indicator if rule else None,
            "values": {str(year): value for year, value in request.input_adjustment.items()},
        },
        "output_indicators": {},
        "summary_before": {},
        "calculation_status": "calculation_failed",
        "cycle_converged": False,
        "iterations": 0,
        "final_difference": None,
        "final_differences": {},
        "error": None,
        "stage_timings": {},
        "engine": getattr(engine, "engine_info", lambda: {"name": type(engine).__name__, "version": "unknown"})(),
        "diagnostics": {},
        "template_fingerprint": file_sha256(template_path) if Path(template_path).is_file() else None,
    }
    try:
        report("open_isolated")
        engine.open_isolated(template_path)
        result["engine"] = getattr(engine, "engine_info", lambda: result["engine"])()
        if cancelled():
            raise CalculationCancelled("测算已取消")
        report("baseline_summary_read")
        result["summary_before"] = engine.read_summary(stage="baseline_summary_read") if read_baseline else {}
        report("write_input")
        for item in request.input_adjustments:
            item_rule = item.get("input_rule")
            if item_rule is None or not item_rule.confirmed:
                raise ValueError("a confirmed input rule is required")
            engine.write_input(item_rule, item.get("values", {}))
        if request.input_adjustment:
            if rule is None or not rule.confirmed:
                raise ValueError("a confirmed input rule is required")
            engine.write_input(rule, request.input_adjustment)
        if cancelled():
            raise CalculationCancelled("测算已取消")
        report("initial_recalculate")
        engine.recalculate(stage="initial_recalculate")
        difference = None
        for iteration in range(1, max_iterations + 1):
            if cancelled():
                raise CalculationCancelled("测算已取消")
            report(f"cycle_iteration_{iteration}")
            engine.copy_cycle_ranges()
            engine.recalculate(stage="recalculate")
            differences = engine.read_cycle_differences()
            difference = max(differences.values(), default=0.0)
            result["iterations"] = iteration
            if difference <= tolerance:
                result["cycle_converged"] = True
                result["calculation_status"] = "valid"
                break
        result["final_difference"] = difference
        result["final_differences"] = differences if difference is not None else {}
        report("result_summary_read")
        result["output_indicators"] = engine.read_summary(stage="result_summary_read")
        if not result["cycle_converged"]:
            result["calculation_status"] = "cycle_not_converged"
    except CalculationCancelled:
        raise
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        try:
            engine.close()
        except Exception:
            pass
        timings = getattr(engine, "stage_timings", None)
        if timings:
            result["stage_timings"] = timings
        result["diagnostics"] = getattr(engine, "diagnostics", lambda: {})()
    return result


class InMemoryWorkbookEngine:
    def __init__(self, *, differences: list[float] | None = None, fails: bool = False, summary_sheet_missing: bool = False):
        self.differences = differences or [0.05]
        self.fails = fails
        self.iteration = 0
        self.adjustments: dict[int, Any] = {}
        self.summary_sheet_missing = summary_sheet_missing
        self.stage_timings: dict[str, list[float]] = {}

    def engine_info(self) -> dict[str, Any]:
        return {"name": "in_memory", "version": "1", "platform": os.name, "production_ready": False}

    def diagnostics(self) -> dict[str, Any]:
        return {"iterations": self.iteration, "available": True}

    def open_isolated(self, template_path: Path) -> None:
        if self.fails:
            raise RuntimeError("engine unavailable")

    def write_input(self, rule: ConfirmedInputRule, adjustment: dict[int, Any]) -> None:
        self.adjustments = adjustment

    def inspect_workbook(self) -> dict[str, Any]:
        worksheets = [{"name": "封面", "index": 1}]
        if not self.summary_sheet_missing:
            worksheets.append({"name": "汇总展示表", "index": 2})
        return {"worksheets": worksheets}

    def read_indicator_catalog(self) -> dict[str, Any]:
        if self.summary_sheet_missing:
            raise ValueError("找不到《汇总展示表》工作表")
        year_mapping = {str(year): column for year, column in zip(range(2026, 2031), "DEFGH")}
        rows = [
            (6, "财务结果", "归母净利润", "亿元", [757.8, 810.6, 866.0, 927.4, 985.2]),
            (53, "重要参数", "10年期国债收益率", "%", [0.0175] * 5),
            (69, "规模假设", "并表口径总资产", "亿元", [108000, 113000, 118000, 124000, 130000]),
            (107, "价格假设", "对公贷款利率", "%", [0.0408] * 5),
            (129, "中收假设", "财富管理AUM规模增速", "%", [0.15, 0.12, 0.11, 0.1, 0.09]),
        ]
        return {
            "worksheet": {"name": "汇总展示表", "index": 2},
            "year_mapping": year_mapping,
            "indicators": [
                {
                    "row": row,
                    "display_name": name,
                    "group": group,
                    "unit": unit,
                    "cell_address": f"B{row}",
                    "year_cells": {str(year): f"{column}{row}" for year, column in zip(range(2026, 2031), "DEFGH")},
                    "year_values": {str(year): value for year, value in zip(range(2026, 2031), values)},
                }
                for row, group, name, unit, values in rows
            ],
        }

    def read_cell_formula_or_value(self, sheet: str, cell: str) -> Any:
        return None

    def recalculate(self, stage: str = "recalculate") -> None:
        return None

    def copy_cycle_ranges(self) -> None:
        self.iteration += 1

    def read_cycle_differences(self) -> dict[str, float]:
        index = min(self.iteration - 1, len(self.differences) - 1)
        return {"profitability": self.differences[index], "segment": self.differences[index]}

    def read_summary(self, stage: str = "summary_read") -> dict[str, Any]:
        return {"利润": {str(year): 100.0 for year in range(2026, 2031)}}

    def close(self) -> None:
        return None


class ExcelComWorkbookEngine:
    RECALC_MODES = ("full_rebuild", "full", "normal")

    def __init__(self, recalc_mode: str = "full_rebuild", *, excel_application: Any = None, workbook: Any = None, expected_template_sha256: str | None = None):
        if recalc_mode not in self.RECALC_MODES:
            raise ValueError(f"未知重算策略: {recalc_mode}")
        self.recalc_mode = recalc_mode
        self.excel = excel_application or (workbook.Application if workbook is not None else None)
        self._owns_excel = excel_application is None and workbook is None
        self.expected_template_sha256 = expected_template_sha256
        self.workbook = workbook
        self._owns_workbook = workbook is None
        self._temp_path: Path | None = None
        self._com_initialized = False
        self._original_inputs: dict[tuple[str, str], Any] = {}
        self._original_cycle_targets: list[tuple[Any, Any]] = []
        self.reset_error: str | None = None
        self.stage_timings: dict[str, list[float]] = {}

    def _record(self, stage: str, elapsed_seconds: float) -> None:
        self.stage_timings.setdefault(stage, []).append(round(elapsed_seconds * 1000, 2))

    def engine_info(self) -> dict[str, Any]:
        version = "unknown"
        if self.excel is not None:
            try:
                version = str(self.excel.Version)
            except Exception:
                pass
        return {"name": "excel_com", "version": version, "platform": "windows", "production_ready": True}

    def diagnostics(self) -> dict[str, Any]:
        return {"available": os.name == "nt", "recalc_mode": self.recalc_mode, "reset_error": self.reset_error}

    def open_isolated(self, template_path: Path) -> None:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
        if self._owns_excel:
            pythoncom.CoInitialize()
            self._com_initialized = True
        if self.expected_template_sha256 and file_sha256(template_path).casefold() != self.expected_template_sha256.casefold():
            raise ValueError("活动模板 SHA-256 校验失败，拒绝 warm_com 测算")
        if self.workbook is not None:
            self._record("template_copy", 0)
            self._record("excel_start", 0)
            self._record("workbook_open", 0)
            return
        started = time.perf_counter()
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        Path(path).unlink(missing_ok=True)
        shutil.copy2(template_path, path)
        self._temp_path = Path(path)
        if self.expected_template_sha256 and file_sha256(self._temp_path).casefold() != self.expected_template_sha256.casefold():
            raise ValueError("隔离副本 SHA-256 校验失败，拒绝 warm_com 测算")
        self._record("template_copy", time.perf_counter() - started)
        started = time.perf_counter()
        if self.excel is None:
            self.excel = win32com.client.DispatchEx("Excel.Application")
            self.excel.Visible = False
            self.excel.DisplayAlerts = False
            self.excel.AskToUpdateLinks = False
            self.excel.EnableEvents = False
        self._record("excel_start", time.perf_counter() - started)
        started = time.perf_counter()
        self.workbook = self.excel.Workbooks.Open(
            str(self._temp_path), UpdateLinks=0, ReadOnly=False, IgnoreReadOnlyRecommended=True
        )
        self._record("workbook_open", time.perf_counter() - started)

    def write_input(self, rule: ConfirmedInputRule, adjustment: dict[int, Any]) -> None:
        started = time.perf_counter()
        sheet = (
            self.workbook.Worksheets(rule.source_sheet_index)
            if rule.source_sheet_index is not None
            else self._worksheet(rule.source_sheet)
        )
        for year, value in adjustment.items():
            try:
                cell = rule.year_cells[year]
            except KeyError as exc:
                raise ValueError(f"rule has no source cell for {year}") from exc
            key = (str(sheet.Name), cell)
            target = sheet.Range(cell)
            if key not in self._original_inputs:
                self._original_inputs[key] = target.Value
            target.Value = value
        self._record("write_input", time.perf_counter() - started)

    def _worksheet(self, name: str):
        try:
            return self.workbook.Worksheets(name)
        except Exception as original:
            wanted = _normalize_sheet_name(name)
            for index in range(1, self.workbook.Worksheets.Count + 1):
                candidate = self.workbook.Worksheets(index)
                if _normalize_sheet_name(str(candidate.Name)) == wanted:
                    return candidate
            raise original

    @staticmethod
    def _text(value: Any) -> str:
        text = str(value or "").strip()
        try:
            return text.encode("latin1").decode("gbk")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text

    def inspect_workbook(self) -> dict[str, Any]:
        return {
            "worksheets": [
                {"name": self._text(self.workbook.Worksheets(index).Name), "index": index}
                for index in range(1, self.workbook.Worksheets.Count + 1)
            ]
        }

    def _summary_worksheet(self):
        try:
            return self._worksheet("汇总展示表")
        except Exception:
            candidate = self.workbook.Worksheets(2) if self.workbook.Worksheets.Count >= 2 else None
            if candidate is not None and self._text(candidate.Name) == "汇总展示表":
                return candidate
            names = [item["name"] for item in self.inspect_workbook()["worksheets"]]
            raise ValueError(f"找不到《汇总展示表》工作表；现有工作表: {names}")

    def read_indicator_catalog(self) -> dict[str, Any]:
        sheet = self._summary_worksheet()
        year_mapping = {str(year): column for year, column in zip(range(2026, 2031), "DEFGH")}
        group = ""
        indicators = []
        sections = []
        contract_validated = False
        for row in range(1, sheet.UsedRange.Rows.Count + 1):
            raw_group = self._text(sheet.Cells(row, 1).Value)
            name = self._text(sheet.Cells(row, 2).Value)
            headers = [self._text(sheet.Cells(row, column).Value) for column in range(4, 9)]
            if headers == [f"{year}年" for year in range(2026, 2031)]:
                contract = [self._text(sheet.Cells(row, column).Value) for column in range(3, 11)]
                normalized = [value.replace(" ", "").upper() for value in contract]
                base_header_valid = not normalized[0] or normalized[0].startswith("2025")
                change_header_valid = "变化" in normalized[6] or "变动" in normalized[6]
                cagr_header_valid = not normalized[7] or "CAGR" in normalized[7] or "复合" in normalized[7] or "增速" in normalized[7]
                if not base_header_valid or not change_header_valid or not cagr_header_valid:
                    raise ValueError(f"汇总展示表 C–J 表头不符合约定: {contract}")
                contract_validated = True
                continue
            if raw_group:
                group = raw_group
            if name == "指标":
                continue
            values = [sheet.Cells(row, column).Value for column in range(4, 9)]
            if name and not any(value is not None for value in values):
                level = section_level(name)
                if level is not None:
                    sections.append({"row": row, "title": name, "level": level})
                continue
            if not name:
                continue
            number_format = str(sheet.Cells(row, 4).NumberFormat or "")
            unit = "%" if "%" in number_format else "未知"
            indicators.append({
                "row": row,
                "display_name": name,
                "group": group,
                "unit": unit,
                "cell_address": f"B{row}",
                "year_cells": {str(year): f"{column}{row}" for year, column in zip(range(2026, 2031), "DEFGH")},
                "year_values": {str(year): value for year, value in zip(range(2026, 2031), values)},
                "result_values": {
                    "2025": sheet.Cells(row, 3).Value,
                    **{str(year): value for year, value in zip(range(2026, 2031), values)},
                    "five_year_change": sheet.Cells(row, 9).Value,
                    "cagr": sheet.Cells(row, 10).Value,
                },
            })
        if not contract_validated:
            raise ValueError("汇总展示表缺少 C–J 约定表头")
        return {
            "worksheet": {"name": "汇总展示表", "index": int(sheet.Index)},
            "year_mapping": year_mapping,
            "indicators": indicators,
            "sections": sections,
        }

    def read_cell_formula_or_value(self, sheet: str, cell: str) -> Any:
        range_ = self._worksheet(sheet).Range(cell)
        formula = range_.Formula
        return formula if isinstance(formula, str) and formula.startswith("=") else range_.Value

    def recalculate(self, stage: str = "recalculate") -> None:
        started = time.perf_counter()
        application = self.workbook.Application
        if self.recalc_mode == "normal":
            application.Calculate()
        elif self.recalc_mode == "full":
            application.CalculateFull()
        else:
            application.CalculateFullRebuild()
        self._record(stage, time.perf_counter() - started)

    def copy_cycle_ranges(self) -> None:
        started = time.perf_counter()
        ranges = [("2026-2030年盈利测算表", "N154:W155", "N160:W161"), ("板块", "H131:Q131", "H132:Q132")]
        for sheet_name, source, target in ranges:
            sheet = self._worksheet(sheet_name)
            target_range = sheet.Range(target)
            if not self._original_cycle_targets:
                self._original_cycle_targets.append((target_range, target_range.Value))
            elif len(self._original_cycle_targets) < len(ranges):
                self._original_cycle_targets.append((target_range, target_range.Value))
            target_range.Value = sheet.Range(source).Value
        self._record("cycle_copy", time.perf_counter() - started)

    def read_cycle_differences(self) -> dict[str, float]:
        started = time.perf_counter()
        ranges = [("profitability", "2026-2030年盈利测算表", "N154:W155", "N160:W161"), ("segment", "板块", "H131:Q131", "H132:Q132")]
        differences = {}
        for name, sheet_name, source, target in ranges:
            sheet = self._worksheet(sheet_name)
            source_values = sheet.Range(source).Value
            target_values = sheet.Range(target).Value
            differences[name] = max(
                abs(float(left or 0) - float(right or 0))
                for source_row, target_row in zip(source_values, target_values)
                for left, right in zip(source_row, target_row)
            )
        self._record("cycle_diff_read", time.perf_counter() - started)
        return differences

    def read_summary(self, stage: str = "summary_read") -> dict[str, Any]:
        started = time.perf_counter()
        sheet = self._summary_worksheet()
        indicators = {}
        row_count = sheet.UsedRange.Rows.Count
        data = sheet.Range(f"A1:J{row_count}").Value if row_count else None
        if data is not None:
            if row_count == 1:
                data = (data,)
            for row in data:
                name = self._text(row[1]) or self._text(row[0])
                values = row[3:8]
                if name and any(value is not None for value in values):
                    indicators[name] = {
                        "2025": row[2],
                        **{str(year): value for year, value in zip(range(2026, 2031), values)},
                        "five_year_change": row[8],
                        "cagr": row[9],
                    }
        self._record(stage, time.perf_counter() - started)
        return indicators

    def close(self) -> None:
        started = time.perf_counter()
        if self.workbook is not None and not self._owns_workbook:
            try:
                for (sheet_name, cell), value in self._original_inputs.items():
                    self.workbook.Worksheets(sheet_name).Range(cell).Value = value
                for target, value in self._original_cycle_targets:
                    target.Value = value
                self.workbook.Application.CalculateFullRebuild()
            except Exception as exc:
                self.reset_error = str(exc)
            finally:
                self._original_inputs.clear()
                self._original_cycle_targets.clear()
                self.workbook = None
                self.excel = None
            self._record("restore_state", time.perf_counter() - started)
            return
        if self.workbook is not None:
            self.workbook.Close(SaveChanges=False)
            self.workbook = None
        if self.excel is not None and self._owns_excel:
            self.excel.Quit()
            self.excel = None
        if self._temp_path:
            self._temp_path.unlink(missing_ok=True)
            self._temp_path = None
        if self._com_initialized:
            import pythoncom  # type: ignore
            pythoncom.CoUninitialize()
            self._com_initialized = False
        self._record("close_cleanup", time.perf_counter() - started)


class WarmExcelWorkerError(RuntimeError):
    pass


class _CombinedCancelToken:
    def __init__(self, *tokens: Any):
        self.tokens = tokens

    def is_set(self) -> bool:
        return any(token is not None and token.is_set() for token in self.tokens)


class WarmExcelWorker:
    """One STA Excel process, one queued calculation at a time, and a fresh isolated workbook per request."""

    def __init__(self, expected_template_sha256: str, *, timeout_seconds: float = 60.0):
        self.worker_id = str(uuid.uuid4())
        self.expected_template_sha256 = expected_template_sha256
        self.timeout_seconds = timeout_seconds
        self._queue: queue.Queue[Any] = queue.Queue()
        self._started = threading.Event()
        self._stopped = threading.Event()
        self._startup_error: str | None = None
        self._startup_elapsed_ms = 0.0
        self._pid: int | None = None
        self._thread = threading.Thread(target=self._run, name=f"warm-excel-{self.worker_id[:8]}", daemon=True)
        self._thread.start()
        atexit.register(self.shutdown)

    @staticmethod
    def _start_excel() -> Any:
        import win32com.client  # type: ignore
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.AskToUpdateLinks = False
        excel.EnableEvents = False
        return excel

    @staticmethod
    def _excel_pid(excel: Any) -> int | None:
        try:
            import win32process  # type: ignore
            return int(win32process.GetWindowThreadProcessId(excel.Hwnd)[1])
        except Exception:
            return None

    def _run(self) -> None:
        import pythoncom  # type: ignore
        pythoncom.CoInitialize()
        excel = None
        host = None
        try:
            try:
                startup_started = time.perf_counter()
                excel = self._start_excel()
                self._startup_elapsed_ms = round((time.perf_counter() - startup_started) * 1000, 2)
                self._pid = self._excel_pid(excel)
            except Exception as exc:
                self._startup_error = str(exc)
            finally:
                self._started.set()
            while True:
                job = self._queue.get()
                if job is None:
                    break
                if job["cancel"].is_set():
                    job["done"].set()
                    continue
                job["started_at"] = time.perf_counter()
                try:
                    if excel is None:
                        excel = self._start_excel()
                        self._pid = self._excel_pid(excel)
                    if host is None:
                        host = ExcelComWorkbookEngine(excel_application=excel, expected_template_sha256=self.expected_template_sha256)
                        host.open_isolated(job["template_path"])
                        warmup_timings = host.stage_timings
                        warmup_timings["excel_start"] = [self._startup_elapsed_ms]
                    else:
                        warmup_timings = None
                    engine = ExcelComWorkbookEngine(workbook=host.workbook, expected_template_sha256=self.expected_template_sha256)
                    job["result"] = run_forward_calculation(
                        engine, job["request"], template_path=job["template_path"], tolerance=job["tolerance"],
                        max_iterations=job["max_iterations"], read_baseline=job["read_baseline"],
                        cancel_token=_CombinedCancelToken(job["cancel"], job["cancel_token"]), progress=job["progress"],
                    )
                    if warmup_timings:
                        job["result"]["stage_timings"] = {**job["result"]["stage_timings"], **warmup_timings}
                    if engine.reset_error:
                        raise WarmExcelWorkerError(f"warm workbook state restore failed: {engine.reset_error}")
                    if job["result"]["calculation_status"] == "calculation_failed":
                        raise WarmExcelWorkerError(job["result"].get("error") or "warm_com calculation failed")
                except CalculationCancelled as exc:
                    job["error"] = exc
                except Exception as exc:
                    job["error"] = WarmExcelWorkerError(str(exc))
                    try:
                        if host is not None:
                            host.close()
                        if excel is not None:
                            excel.Quit()
                    except Exception:
                        pass
                    excel = None
                    host = None
                    self._pid = None
                finally:
                    job["done"].set()
        finally:
            try:
                if host is not None:
                    host.close()
                if excel is not None:
                    excel.Quit()
            except Exception:
                pass
            self._pid = None
            pythoncom.CoUninitialize()
            self._stopped.set()

    def health(self) -> dict[str, Any]:
        self._started.wait(15)
        return {
            "healthy": self._thread.is_alive() and self._startup_error is None and self._pid is not None,
            "worker_id": self.worker_id,
            "pid": self._pid,
            "queue_depth": self._queue.qsize(),
            "error": self._startup_error,
        }

    def calculate(
        self, request: CalculationRequest, *, template_path: Path, tolerance: float = 0.1,
        max_iterations: int = 20, read_baseline: bool = True, cancel_token: Any = None,
        progress: Any = None, timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        health = self.health()
        if not health["healthy"]:
            raise WarmExcelWorkerError(health["error"] or "warm Excel worker unavailable")
        job = {
            "request": request, "template_path": Path(template_path), "tolerance": tolerance,
            "max_iterations": max_iterations, "read_baseline": read_baseline, "cancel_token": cancel_token,
            "progress": progress, "cancel": threading.Event(), "done": threading.Event(), "queued_at": time.perf_counter(),
            "started_at": None, "result": None, "error": None,
        }
        self._queue.put(job)
        deadline = time.perf_counter() + (timeout_seconds or self.timeout_seconds)
        while not job["done"].wait(0.05):
            if cancel_token is not None and cancel_token.is_set():
                job["cancel"].set()
                self.cleanup_orphan()
                raise CalculationCancelled("测算已取消")
            if time.perf_counter() >= deadline:
                job["cancel"].set()
                self.cleanup_orphan()
                raise WarmExcelWorkerError("warm_com worker timeout; Excel process terminated and will rebuild")
        if job["error"]:
            raise job["error"]
        result = job["result"]
        result["engine_mode"] = "warm_com"
        result["worker_id"] = self.worker_id
        result["queue_wait_ms"] = round(((job["started_at"] or time.perf_counter()) - job["queued_at"]) * 1000, 2)
        result["cancel_status"] = "not_requested"
        return result

    def cleanup_orphan(self) -> bool:
        pid = self._pid
        if not pid:
            return False
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._pid = None
        return completed.returncode == 0

    def shutdown(self) -> None:
        if self._stopped.is_set():
            return
        pid = self._pid
        self._queue.put(None)
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            self.cleanup_orphan()
            return
        if pid:
            deadline = time.time() + 5
            while time.time() < deadline:
                completed = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                    check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if f'"{pid}"' not in completed.stdout:
                    return
                time.sleep(.1)
            self._pid = pid
            self.cleanup_orphan()
            deadline = time.time() + 2
            while time.time() < deadline:
                completed = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                    check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if f'"{pid}"' not in completed.stdout:
                    break
                time.sleep(.1)
