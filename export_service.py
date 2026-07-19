from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook


METADATA_FIELDS = (
    "template_version_id", "template_fingerprint", "rule_publication_id",
    "scenario_id", "scenario_type", "calculation_time", "validation_state",
)


class ExportService:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def create(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        metadata = self._metadata(payload)
        file_id = str(uuid.uuid4())
        path = self.directory / f"{kind}-{file_id}.xlsx"
        workbook = Workbook()
        workbook.remove(workbook.active)
        self._sheet(workbook, "Metadata", ("field", "value"), ((key, metadata[key]) for key in METADATA_FIELDS))
        if kind == "scenario":
            self._scenario(workbook, payload)
        elif kind == "reverse":
            self._reverse(workbook, payload)
        elif kind == "comparison":
            self._comparison(workbook, payload)
        else:
            raise ValueError("未知导出类型")
        workbook.save(path)
        return {"file_id": file_id, "file_name": path.name, "path": str(path), "download_url": f"/api/exports/{file_id}"}

    def resolve(self, file_id: str) -> Path:
        matches = list(self.directory.glob(f"*-{file_id}.xlsx"))
        if len(matches) != 1:
            raise ValueError("导出文件不存在")
        return matches[0]

    @staticmethod
    def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
        source = payload.get("metadata") or payload.get("scenario_draft") or payload
        metadata = {key: source.get(key) for key in METADATA_FIELDS}
        baseline = next((item for item in payload.get("scenarios", []) if item.get("scenario_id") == payload.get("baseline_scenario_id")), None) or next(iter(payload.get("scenarios", [])), {})
        metadata["template_version_id"] = metadata["template_version_id"] or baseline.get("template_version_id")
        metadata["template_fingerprint"] = metadata["template_fingerprint"] or baseline.get("template_fingerprint") or payload.get("template", {}).get("fingerprint")
        metadata["rule_publication_id"] = metadata["rule_publication_id"] or baseline.get("rule_publication_id")
        metadata["scenario_id"] = metadata["scenario_id"] or payload.get("comparison_id") or "current"
        metadata["scenario_type"] = metadata["scenario_type"] or ("comparison" if payload.get("comparison_id") else "custom")
        metadata["calculation_time"] = metadata["calculation_time"] or payload.get("calculation_details", {}).get("finished_at") or datetime.now(timezone.utc).isoformat()
        metadata["validation_state"] = metadata["validation_state"] or payload.get("trust", {}).get("status")
        missing = [key for key, value in metadata.items() if value in (None, "")]
        if missing:
            raise ValueError(f"导出元数据不完整: {', '.join(missing)}")
        return metadata

    @staticmethod
    def _sheet(workbook: Workbook, name: str, headers: tuple[str, ...], rows: Any) -> None:
        sheet = workbook.create_sheet(name)
        sheet.append(headers)
        for row in rows:
            sheet.append(tuple(row))
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions

    def _scenario(self, workbook: Workbook, payload: dict[str, Any]) -> None:
        draft = payload.get("scenario_draft") or payload
        adjustments = draft.get("input_adjustments") or {}
        results = draft.get("calculation_result_snapshot") or payload.get("results") or {}
        if not results:
            raise ValueError("当前场景没有可导出的测算结果")
        self._sheet(workbook, "Inputs", ("indicator", "year", "value"), ((name, year, value) for name, values in adjustments.items() for year, value in values.items()))
        self._sheet(workbook, "Results", ("indicator", "year", "value"), ((name, year, value) for name, values in results.items() for year, value in values.items()))
        details = payload.get("details") or []
        self._sheet(workbook, "Details", ("group", "indicator", "year", "value"), ((item.get("group"), item.get("name"), year, value) for item in details for year, value in (item.get("values") or {}).items()))

    def _reverse(self, workbook: Workbook, payload: dict[str, Any]) -> None:
        variable = payload.get("variable") or {}
        variables = payload.get("variables") or ([variable] if variable else [])
        if not variables or not payload.get("constraints"):
            raise ValueError("反向测算结果不完整")
        if payload.get("variables"):
            self._sheet(workbook, "Variables", ("indicator", "year", "priority", "baseline", "suggested", "adjustment", "lower", "upper", "hit_boundary", "linkage_strategy"), ((item.get("indicator_name"), item.get("year"), item.get("priority"), item.get("baseline_value"), item.get("suggested_value"), item.get("adjustment"), item.get("lower"), item.get("upper"), item.get("hit_boundary"), item.get("linkage_strategy")) for item in variables))
        else:
            self._sheet(workbook, "Variable", ("field", "value"), ((key, variable.get(key)) for key in ("indicator_name", "year", "required_value", "adjustment")))
        self._sheet(workbook, "Results", ("field", "value"), (("feasible", payload.get("feasible")), ("soft_deviation", payload.get("soft_deviation")), ("search_count", payload.get("search_count"))))
        self._sheet(workbook, "Constraints", ("indicator", "year", "kind", "hard", "target", "actual", "hit", "deviation"), ((item.get("indicator_name"), item.get("year"), item.get("kind"), item.get("hard"), item.get("value"), item.get("actual"), item.get("hit"), item.get("deviation")) for item in payload["constraints"]))

    def _comparison(self, workbook: Workbook, payload: dict[str, Any]) -> None:
        scenarios = payload.get("scenarios") or []
        details = payload.get("details") or []
        if len(scenarios) < 2 or not details:
            raise ValueError("多场景对比结果不完整")
        self._sheet(workbook, "Scenarios", ("scenario_id", "name", "scenario_type", "template_version_id", "rule_publication_id", "validation_state", "source", "failure_reason"), ((item.get("scenario_id"), item.get("name"), item.get("scenario_type"), item.get("template_version_id"), item.get("rule_publication_id"), item.get("validation_state"), item.get("source"), item.get("failure_reason")) for item in scenarios))
        self._sheet(workbook, "Comparison", ("group", "indicator", "scenario_id", "scenario_name", "year", "value", "baseline_difference", "validation_state"), ((metric.get("group"), metric.get("name"), row.get("scenario_id"), row.get("name"), year, value, (row.get("differences") or {}).get(year), next((item.get("validation_state") for item in scenarios if item.get("scenario_id") == row.get("scenario_id")), None)) for metric in details for row in metric.get("scenarios", []) for year, value in (row.get("values") or {}).items()))
