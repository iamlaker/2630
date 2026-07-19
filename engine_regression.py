from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from forecast_engine import CalculationRequest, WorkbookEngine, run_forward_calculation


@dataclass(frozen=True)
class EngineRegressionSample:
    sample_id: str
    publication_id: str
    template_fingerprint: str
    template_path: Path
    request: CalculationRequest


def _input_snapshot(request: CalculationRequest) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for item in request.input_adjustments:
        rule = item["input_rule"]
        rows[rule.indicator] = {str(year): value for year, value in item.get("values", {}).items()}
    if request.input_rule and request.input_adjustment:
        rows[request.input_rule.indicator] = {str(year): value for year, value in request.input_adjustment.items()}
    return rows


def execute_regression_sample(engine: WorkbookEngine, sample: EngineRegressionSample) -> dict[str, Any]:
    result = run_forward_calculation(engine, sample.request, template_path=sample.template_path)
    return {
        "sample_id": sample.sample_id,
        "publication_id": sample.publication_id,
        "template_fingerprint": sample.template_fingerprint,
        "actual_template_fingerprint": result.get("template_fingerprint"),
        "engine": result.get("engine"),
        "inputs": _input_snapshot(sample.request),
        "outputs": result["output_indicators"],
        "cycle": {
            "converged": result["cycle_converged"], "iterations": result["iterations"],
            "final_differences": result["final_differences"],
        },
        "calculation_status": result["calculation_status"],
        "stage_timings": result.get("stage_timings", {}),
        "diagnostics": result.get("diagnostics", {}),
        "error": result.get("error"),
    }


def _difference_rows(
    category: str, reference: dict[str, dict[str, Any]], candidate: dict[str, dict[str, Any]],
    reference_engine: dict[str, Any], candidate_engine: dict[str, Any], tolerance: float,
) -> list[dict[str, Any]]:
    rows = []
    for indicator in sorted(set(reference) | set(candidate)):
        left_values, right_values = reference.get(indicator, {}), candidate.get(indicator, {})
        for year in sorted(set(left_values) | set(right_values)):
            left, right = left_values.get(year), right_values.get(year)
            if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                difference = abs(float(left) - float(right))
                outside = difference > tolerance
            else:
                difference = None if left == right else "value_mismatch"
                outside = left != right
            if outside:
                rows.append({
                    "category": category, "indicator": indicator, "year": str(year),
                    "reference_value": left, "candidate_value": right, "absolute_difference": difference,
                    "tolerance": tolerance, "reference_engine": reference_engine["name"],
                    "reference_version": reference_engine.get("version"), "candidate_engine": candidate_engine["name"],
                    "candidate_version": candidate_engine.get("version"),
                })
    return rows


def compare_engine_results(
    reference: dict[str, Any], candidate: dict[str, Any], *,
    output_tolerance: float = 1e-6, input_tolerance: float = 0.0, cycle_tolerance: float = 0.1,
) -> dict[str, Any]:
    if min(output_tolerance, input_tolerance, cycle_tolerance) < 0:
        raise ValueError("engine regression tolerances must be non-negative")
    reference_engine = reference["engine"]
    candidate_engine = candidate["engine"]
    differences = _difference_rows("input", reference["inputs"], candidate["inputs"], reference_engine, candidate_engine, input_tolerance)
    differences.extend(_difference_rows("output", reference["outputs"], candidate["outputs"], reference_engine, candidate_engine, output_tolerance))
    differences.extend(_difference_rows(
        "cycle", {"cycle_difference": reference["cycle"]["final_differences"]},
        {"cycle_difference": candidate["cycle"]["final_differences"]}, reference_engine, candidate_engine, cycle_tolerance,
    ))
    fingerprint_match = reference["template_fingerprint"].casefold() == candidate["template_fingerprint"].casefold()
    if not fingerprint_match:
        differences.append({
            "category": "template", "indicator": "template_fingerprint", "year": None,
            "reference_value": reference["template_fingerprint"], "candidate_value": candidate["template_fingerprint"],
            "absolute_difference": "fingerprint_mismatch", "tolerance": 0,
            "reference_engine": reference_engine["name"], "reference_version": reference_engine.get("version"),
            "candidate_engine": candidate_engine["name"], "candidate_version": candidate_engine.get("version"),
        })
    expected_fingerprint = reference["template_fingerprint"].casefold()
    for label, result, engine in (("reference", reference, reference_engine), ("candidate", candidate, candidate_engine)):
        actual = str(result.get("actual_template_fingerprint") or "").casefold()
        if actual != expected_fingerprint:
            differences.append({
                "category": "template", "indicator": f"{label}_actual_template_fingerprint", "year": None,
                "reference_value": reference["template_fingerprint"], "candidate_value": result.get("actual_template_fingerprint"),
                "absolute_difference": "fingerprint_mismatch", "tolerance": 0,
                "reference_engine": reference_engine["name"], "reference_version": reference_engine.get("version"),
                "candidate_engine": engine["name"], "candidate_version": engine.get("version"),
            })
    valid_runs = reference["calculation_status"] == candidate["calculation_status"] == "valid"
    status = "valid" if valid_runs and not differences else "engine_difference"
    production_ready = status == "valid" and bool(candidate_engine.get("production_ready"))
    return {
        "validation_state": status,
        "reason": "engines match within configured tolerances" if status == "valid" else f"{len(differences)} engine differences exceed tolerance",
        "sample_id": reference["sample_id"], "publication_id": reference["publication_id"],
        "template_fingerprint": reference["template_fingerprint"], "reference_engine": reference_engine,
        "candidate_engine": candidate_engine, "tolerances": {"input": input_tolerance, "output": output_tolerance, "cycle": cycle_tolerance},
        "differences": differences, "difference_count": len(differences), "production_ready": production_ready,
        "production_ready_reason": None if production_ready else "candidate is unavailable, differs from baseline, or is not approved for production",
    }


def unavailable_candidate_validation(sample: EngineRegressionSample, engine: Any) -> dict[str, Any]:
    info, diagnostics = engine.engine_info(), engine.diagnostics()
    return {
        "validation_state": "engine_difference", "reason": diagnostics.get("error") or "candidate engine unavailable",
        "sample_id": sample.sample_id, "publication_id": sample.publication_id,
        "template_fingerprint": sample.template_fingerprint, "reference_engine": {"name": "excel_com", "version": "not_run"},
        "inputs": _input_snapshot(sample.request),
        "candidate_engine": info, "tolerances": {"input": 0.0, "output": 1e-6, "cycle": 0.1},
        "differences": [{
            "category": "availability", "indicator": "engine", "year": None,
            "reference_value": "available", "candidate_value": diagnostics.get("error") or "unavailable",
            "absolute_difference": "engine_unavailable", "tolerance": 0,
            "reference_engine": "excel_com", "reference_version": "not_run",
            "candidate_engine": info["name"], "candidate_version": info.get("version"),
        }],
        "difference_count": 1, "production_ready": False,
        "production_ready_reason": "Ubuntu candidate cannot pass baseline regression until its runtime is installed and validated",
        "candidate_diagnostics": diagnostics,
    }
