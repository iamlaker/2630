from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from forecast_engine import ConfirmedInputRule, _normalize_sheet_name, file_sha256, section_level


class UbuntuEngineUnavailable(RuntimeError):
    pass


class LibreOfficeCalcEngine:
    """UNO-backed Ubuntu candidate implementing the shared WorkbookEngine contract."""

    def __init__(self, *, expected_template_sha256: str | None = None, soffice_path: str | None = None):
        self.soffice_path = soffice_path or shutil.which("soffice") or shutil.which("libreoffice")
        self.expected_template_sha256 = expected_template_sha256
        self.process: subprocess.Popen[Any] | None = None
        self.desktop = None
        self.workbook = None
        self._temp_path: Path | None = None
        self.stage_timings: dict[str, list[float]] = {}
        self._error: str | None = None
        self._version = self._detect_version()

    def _record(self, stage: str, started: float) -> None:
        self.stage_timings.setdefault(stage, []).append(round((time.perf_counter() - started) * 1000, 2))

    def _detect_version(self) -> str:
        if not self.soffice_path:
            return "unavailable"
        completed = subprocess.run([self.soffice_path, "--version"], capture_output=True, text=True, check=False)
        return (completed.stdout or completed.stderr).strip() or "unknown"

    def engine_info(self) -> dict[str, Any]:
        return {"name": "libreoffice_calc", "version": self._version, "platform": "ubuntu", "production_ready": False}

    def diagnostics(self) -> dict[str, Any]:
        return {
            "available": bool(self.soffice_path), "binary": self.soffice_path, "error": self._error,
            "production_ready": False, "production_ready_reason": "baseline regression has not passed",
        }

    def _require_available(self) -> None:
        if not self.soffice_path:
            raise UbuntuEngineUnavailable("LibreOffice soffice executable is not installed or not on PATH")

    @staticmethod
    def _property(name: str, value: Any) -> Any:
        import uno  # type: ignore
        item = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
        item.Name, item.Value = name, value
        return item

    def open_isolated(self, template_path: Path) -> None:
        self._require_available()
        if self.expected_template_sha256 and file_sha256(template_path).casefold() != self.expected_template_sha256.casefold():
            raise ValueError("activity template SHA-256 mismatch")
        try:
            import uno  # type: ignore
        except ImportError as exc:
            raise UbuntuEngineUnavailable("python-uno is not installed for this Python runtime") from exc
        started = time.perf_counter()
        fd, path = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        shutil.copy2(template_path, path)
        self._temp_path = Path(path)
        self._record("template_copy", started)
        port = 20000 + os.getpid() % 20000
        accept = f"socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext"
        self.process = subprocess.Popen(
            [self.soffice_path, "--headless", f"--accept={accept}", "--norestore", "--nodefault", "--nofirststartwizard"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        local_context = uno.getComponentContext()
        resolver = local_context.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local_context)
        deadline, context = time.time() + 15, None
        while time.time() < deadline:
            try:
                context = resolver.resolve(f"uno:socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext")
                break
            except Exception:
                time.sleep(.1)
        if context is None:
            raise UbuntuEngineUnavailable("LibreOffice UNO listener did not become ready")
        service_manager = context.ServiceManager
        self.desktop = service_manager.createInstanceWithContext("com.sun.star.frame.Desktop", context)
        self.workbook = self.desktop.loadComponentFromURL(
            uno.systemPathToFileUrl(str(self._temp_path)), "_blank", 0,
            (self._property("Hidden", True), self._property("ReadOnly", False)),
        )
        if self.workbook is None:
            raise RuntimeError("LibreOffice could not load isolated workbook")
        self._record("workbook_open", started)

    def _sheet(self, name: str):
        sheets = self.workbook.getSheets()
        if sheets.hasByName(name):
            return sheets.getByName(name)
        wanted = _normalize_sheet_name(name)
        for candidate in sheets.getElementNames():
            if _normalize_sheet_name(candidate) == wanted:
                return sheets.getByName(candidate)
        raise ValueError(f"worksheet not found: {name}")

    def inspect_workbook(self) -> dict[str, Any]:
        return {"worksheets": [{"name": name, "index": index + 1} for index, name in enumerate(self.workbook.getSheets().getElementNames())]}

    def read_indicator_catalog(self) -> dict[str, Any]:
        sheet = self._sheet("汇总展示表")
        cursor = sheet.createCursor(); cursor.gotoEndOfUsedArea(True)
        rows = sheet.getCellRangeByPosition(0, 0, 7, cursor.RangeAddress.EndRow).getDataArray()
        indicators, sections, group = [], [], ""
        for index, row in enumerate(rows, 1):
            raw_group, name = str(row[0] or "").strip(), str(row[1] or "").strip()
            if raw_group: group = raw_group
            if name == "指标": continue
            values = row[3:8]
            if name and not any(value != "" for value in values):
                level = section_level(name)
                if level is not None:
                    sections.append({"row": index, "title": name, "level": level})
                continue
            if name:
                indicators.append({"row": index, "display_name": name, "group": group, "unit": "未知", "cell_address": f"B{index}", "year_cells": {str(year): f"{column}{index}" for year, column in zip(range(2026, 2031), "DEFGH")}, "year_values": {str(year): value for year, value in zip(range(2026, 2031), values)}})
        sheet_index = int(sheet.getCellByPosition(0, 0).getCellAddress().Sheet) + 1
        return {"worksheet": {"name": "汇总展示表", "index": sheet_index}, "year_mapping": {str(year): column for year, column in zip(range(2026, 2031), "DEFGH")}, "indicators": indicators, "sections": sections}

    def read_cell_formula_or_value(self, sheet: str, cell: str) -> Any:
        target = self._sheet(sheet).getCellRangeByName(cell)
        formula = target.getFormula()
        return formula if formula.startswith("=") else target.getValue() if target.getType().value == 1 else target.getString()

    def write_input(self, rule: ConfirmedInputRule, adjustment: dict[int, Any]) -> None:
        started = time.perf_counter(); sheet = self._sheet(rule.source_sheet)
        for year, value in adjustment.items():
            target = sheet.getCellRangeByName(rule.year_cells[year])
            target.setValue(float(value)) if isinstance(value, (int, float)) else target.setString(str(value))
        self._record("write_input", started)

    def recalculate(self, stage: str = "recalculate") -> None:
        started = time.perf_counter(); self.workbook.calculateAll(); self._record(stage, started)

    def copy_cycle_ranges(self) -> None:
        started = time.perf_counter()
        for sheet_name, source, target in (("2026-2030年盈利测算表", "N154:W155", "N160:W161"), ("板块", "H131:Q131", "H132:Q132")):
            sheet = self._sheet(sheet_name); sheet.getCellRangeByName(target).setDataArray(sheet.getCellRangeByName(source).getDataArray())
        self._record("cycle_copy", started)

    def read_cycle_differences(self) -> dict[str, float]:
        started = time.perf_counter(); differences = {}
        for name, sheet_name, source, target in (("profitability", "2026-2030年盈利测算表", "N154:W155", "N160:W161"), ("segment", "板块", "H131:Q131", "H132:Q132")):
            sheet = self._sheet(sheet_name)
            left, right = sheet.getCellRangeByName(source).getDataArray(), sheet.getCellRangeByName(target).getDataArray()
            differences[name] = max(abs(float(a or 0) - float(b or 0)) for left_row, right_row in zip(left, right) for a, b in zip(left_row, right_row))
        self._record("cycle_diff_read", started); return differences

    def read_summary(self, stage: str = "summary_read") -> dict[str, Any]:
        started = time.perf_counter(); sheet = self._sheet("汇总展示表")
        cursor = sheet.createCursor(); cursor.gotoEndOfUsedArea(True)
        rows = sheet.getCellRangeByPosition(0, 0, 7, cursor.RangeAddress.EndRow).getDataArray()
        result = {}
        for row in rows:
            name = str(row[1] or row[0] or "").strip(); values = row[3:8]
            if name and any(value != "" for value in values): result[name] = {str(year): value for year, value in zip(range(2026, 2031), values)}
        self._record(stage, started); return result

    def close(self) -> None:
        started = time.perf_counter()
        try:
            if self.workbook is not None: self.workbook.close(True)
        except Exception as exc:
            self._error = str(exc)
        finally:
            self.workbook = None
            if self.process is not None:
                self.process.terminate()
                try: self.process.wait(timeout=5)
                except subprocess.TimeoutExpired: self.process.kill()
                self.process = None
            if self._temp_path: self._temp_path.unlink(missing_ok=True); self._temp_path = None
            self._record("close_cleanup", started)
