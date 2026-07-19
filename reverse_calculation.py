from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Callable


@dataclass(frozen=True)
class ReverseConstraint:
    indicator_id: str
    indicator_name: str
    year: str
    kind: str
    value: float
    enabled: bool = True
    hard: bool = True
    tolerance: float = 0.0
    indicator_type: str = "output"


def evaluate_constraints(constraints: list[ReverseConstraint], values: Callable[[ReverseConstraint], float]) -> tuple[list[dict[str, Any]], float, float]:
    rows, hard_violation, soft_deviation = [], 0.0, 0.0
    for constraint in constraints:
        if not constraint.enabled:
            rows.append({**constraint.__dict__, "actual": None, "hit": None, "deviation": 0.0})
            continue
        actual = float(values(constraint))
        if constraint.kind == "min":
            deviation = max(0.0, constraint.value - actual - constraint.tolerance)
        elif constraint.kind == "max":
            deviation = max(0.0, actual - constraint.value - constraint.tolerance)
        elif constraint.kind == "target":
            deviation = max(0.0, abs(actual - constraint.value) - constraint.tolerance)
        else:
            raise ValueError(f"未知约束类型: {constraint.kind}")
        hit = deviation <= 0
        rows.append({**constraint.__dict__, "actual": actual, "hit": hit, "deviation": deviation})
        if constraint.hard:
            hard_violation += deviation
        else:
            soft_deviation += deviation
    return rows, hard_violation, soft_deviation


def search_single_variable(*, lower: float, upper: float, evaluate: Callable[[float, int], dict[str, Any]], max_evaluations: int = 25, initial: float | None = None) -> dict[str, Any]:
    if lower > upper:
        raise ValueError("变量允许范围无效")
    if max_evaluations < 3:
        raise ValueError("搜索次数至少为 3")
    initial = float(initial if initial is not None else (lower + upper) / 2)
    if not lower <= initial <= upper:
        raise ValueError("变量初始值必须位于允许范围内")
    candidates = [lower + (upper - lower) * index / 8 for index in range(9)] if lower != upper else [lower]
    candidates = sorted({initial, *candidates}, key=lambda value: (abs(value - initial), value))
    samples = []
    while candidates and len(samples) < max_evaluations:
        value = candidates.pop(0)
        if any(abs(value - sample["variable_value"]) <= 1e-12 for sample in samples):
            continue
        sample = evaluate(value, len(samples) + 1)
        sample["variable_value"] = value
        samples.append(sample)
        samples.sort(key=lambda item: item["variable_value"])
        if not candidates and len(samples) < max_evaluations and len(samples) > 1:
            ranked = sorted(samples, key=lambda item: (item["hard_violation"], item["soft_deviation"], abs(item["variable_value"] - initial)))
            best_index = samples.index(ranked[0])
            left = samples[max(0, best_index - 1)]["variable_value"]
            right = samples[min(len(samples) - 1, best_index + 1)]["variable_value"]
            midpoint = ranked[0]["variable_value"]
            candidates = sorted({(left + midpoint) / 2, (midpoint + right) / 2}, key=lambda value: abs(value - midpoint))
    best = min(samples, key=lambda item: (item["hard_violation"], item["soft_deviation"], abs(item["variable_value"] - initial)))
    return {**best, "feasible": best["hard_violation"] <= 0, "search_count": len(samples)}


def variable_candidates(*, lower: float, upper: float, baseline: float, step: float | None = None, candidates: list[float] | None = None) -> list[float]:
    if lower > upper:
        raise ValueError("变量允许范围无效")
    if not lower <= baseline <= upper:
        raise ValueError("变量基准值超出允许范围")
    if candidates:
        values = [float(value) for value in candidates[:100]]
    elif step:
        if step <= 0:
            raise ValueError("变量步长必须大于 0")
        count = int((upper - lower) / step)
        indexes = range(count + 1) if count <= 100 else sorted({round(index * count / 100) for index in range(101)})
        values = [lower + index * step for index in indexes]
        if not values or values[-1] < upper - 1e-12:
            values.append(upper)
    else:
        values = [lower + (upper - lower) * index / 4 for index in range(5)] if lower != upper else [lower]
    return sorted({baseline, *(value for value in values if lower <= value <= upper)}, key=lambda value: (abs(value - baseline), value))


def search_priority_variables(*, variables: list[dict[str, Any]], evaluate: Callable[[dict[str, float], int], dict[str, Any]], max_evaluations: int = 15) -> dict[str, Any]:
    if not variables:
        raise ValueError("至少配置一个可调变量")
    if not 2 <= max_evaluations <= 20:
        raise ValueError("v2 最大测算次数必须在 2–20 之间")
    ordered = sorted(variables, key=lambda item: (item["priority"], item["order"]))
    current_values = {item["key"]: item["baseline"] for item in ordered}
    samples = []

    def run(values: dict[str, float]) -> dict[str, Any]:
        sample = evaluate(values, len(samples) + 1)
        sample["variable_values"] = dict(values)
        samples.append(sample)
        return sample

    def rank(sample: dict[str, Any]) -> tuple[Any, ...]:
        changed = [variable for variable in ordered if abs(sample["variable_values"][variable["key"]] - variable["baseline"]) > 1e-12]
        return (
            sample["hard_violation"],
            sample["soft_deviation"],
            len(changed),
            sum(abs(sample["variable_values"][variable["key"]] - variable["baseline"]) for variable in changed),
            sum(variable["priority"] for variable in changed),
        )

    best = run(current_values)
    path = []
    priorities = sorted({item["priority"] for item in ordered})
    for priority_index, priority in enumerate(priorities):
        if (best["hard_violation"] <= 0 and best["soft_deviation"] <= 0) or len(samples) >= max_evaluations:
            break
        group = [item for item in ordered if item["priority"] == priority]
        remaining_groups = len(priorities) - priority_index
        allowance = max(1, (max_evaluations - len(samples)) // remaining_groups)
        candidate_sets = []
        for variable in group:
            candidates = list(variable["candidates"])
            if len(candidates) > max(2, allowance):
                size = max(2, allowance)
                indexes = {round(index * (len(candidates) - 1) / (size - 1)) for index in range(size)}
                candidates = [candidates[index] for index in sorted(indexes)]
            candidate_sets.append(candidates)
        combinations = [values for values in product(*candidate_sets) if any(abs(value - current_values[variable["key"]]) > 1e-12 for variable, value in zip(group, values))]
        combinations.sort(key=lambda values: (sum(abs(value - variable["baseline"]) > 1e-12 for variable, value in zip(group, values)), sum(abs(value - variable["baseline"]) for variable, value in zip(group, values))))
        combinations = combinations[:allowance]
        before = best
        level_samples = []
        for values in combinations:
            sample = run({**current_values, **{variable["key"]: value for variable, value in zip(group, values)}})
            level_samples.append(sample)
            if sample["hard_violation"] <= 0 and sample["soft_deviation"] <= 0:
                break
            if len(samples) >= max_evaluations:
                break
        if level_samples:
            candidate = min(level_samples, key=rank)
            if rank(candidate) < rank(best):
                best = candidate
                current_values = dict(candidate["variable_values"])
                for variable in group:
                    if abs(before["variable_values"][variable["key"]] - current_values[variable["key"]]) > 1e-12:
                        path.append({"order": len(path) + 1, "key": variable["key"], "priority": variable["priority"], "from_value": before["variable_values"][variable["key"]], "to_value": current_values[variable["key"]], "hard_violation_before": before["hard_violation"], "hard_violation_after": best["hard_violation"]})
    closest = min(samples, key=rank)
    return {
        **closest,
        "feasible": closest["hard_violation"] <= 0,
        "search_count": len(samples),
        "adjustment_path": path,
        "searched_ranges": [{"key": item["key"], "lower": item["lower"], "upper": item["upper"], "candidate_count": len(item["candidates"])} for item in ordered],
    }
