"""Task12 validation report for the activity publication and Task14 +10bp sample."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from bench_forward_perf import ADJUSTED_VALUES, load_sample
from engine_regression import EngineRegressionSample, compare_engine_results, execute_regression_sample, unavailable_candidate_validation
from forecast_engine import CalculationRequest, ExcelComWorkbookEngine
from ubuntu_engine import LibreOfficeCalcEngine


ROOT = Path(__file__).resolve().parent


def main() -> None:
    loaded = load_sample()
    sample = EngineRegressionSample(
        sample_id="task14-10y-treasury-2026-plus-10bp", publication_id=loaded["publication_id"],
        template_fingerprint=loaded["template_fingerprint"], template_path=loaded["template_path"],
        request=CalculationRequest(input_adjustments=[{"input_rule": loaded["rule"], "values": ADJUSTED_VALUES}]),
    )
    candidate = LibreOfficeCalcEngine(expected_template_sha256=sample.template_fingerprint)
    if not candidate.diagnostics()["available"]:
        validation = unavailable_candidate_validation(sample, candidate)
    else:
        reference = execute_regression_sample(ExcelComWorkbookEngine(), sample)
        candidate_result = execute_regression_sample(candidate, sample)
        validation = compare_engine_results(reference, candidate_result, output_tolerance=1e-6, cycle_tolerance=.1)
        validation["reference_result"] = reference
        validation["candidate_result"] = candidate_result
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), **validation}
    out_dir = ROOT / ".scratch" / "perf"; out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"ubuntu-engine-validation-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(path), "validation_state": report["validation_state"], "production_ready": report["production_ready"], "reason": report["reason"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
