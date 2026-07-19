from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rule_store import RuleStore


YEARS = tuple(range(2026, 2031))
STATUSES = {"pending_confirmation", "confirmed", "rejected", "changed", "unsupported"}
LINKAGE_STRATEGIES = {"independent", "same_delta", "same_value", "baseline_ratio"}
REFERENCE = re.compile(r"(?:(?:'([^']+)'|([\w\u4e00-\u9fff -]+))!)?\$?([A-Z]{1,3})\$?(\d+)")
READ_ERROR = "__formula_read_error__"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FormulaGraph:
    def __init__(self, cells: dict[tuple[str, str], Any]):
        self.cells = {(sheet, cell.replace("$", "").upper()): value for (sheet, cell), value in cells.items()}

    def trace(self, sheet: str, cell: str, *, max_depth: int = 25) -> dict[str, Any]:
        chains: list[list[dict[str, Any]]] = []
        cycle_detected = False
        max_depth_reached = False
        unparseable = False
        read_error = False
        processed_formula = False

        def visit(current_sheet: str, current_cell: str, path: list[dict[str, Any]], active: set[tuple[str, str]], depth: int) -> None:
            nonlocal cycle_detected, max_depth_reached, unparseable, read_error, processed_formula
            key = (current_sheet, current_cell.replace("$", "").upper())
            value = self.cells.get(key)
            node_type = "read_error" if isinstance(value, dict) and READ_ERROR in value else "empty" if value is None else "formula" if isinstance(value, str) and value.startswith("=") else "constant"
            node = {"sheet": current_sheet, "cell": key[1], "type": node_type, "formula": value if node_type == "formula" else None}
            next_path = [*path, node]
            if key in active:
                cycle_detected = True
                chains.append(next_path[:-1] + [{**node, "type": "cycle"}])
                return
            if depth >= max_depth:
                max_depth_reached = True
                chains.append(next_path[:-1] + [{**node, "type": "max_depth"}])
                return
            if node_type != "formula":
                read_error = read_error or node_type == "read_error"
                chains.append(next_path)
                return
            if any(function in value.upper() for function in ("INDIRECT(", "OFFSET(")):
                unparseable = True
                chains.append(next_path[:-1] + [{**node, "type": "unparseable"}])
                return
            references = []
            for match in REFERENCE.finditer(value[1:]):
                reference_sheet = match.group(1) or match.group(2) or current_sheet
                references.append((reference_sheet.strip(), f"{match.group(3)}{match.group(4)}"))
            if not references:
                unparseable = True
                chains.append(next_path[:-1] + [{**node, "type": "unparseable"}])
                return
            reference_texts = [match.group(0).replace("$", "") for match in REFERENCE.finditer(value[1:])]
            formula_body = value[1:].replace("$", "").strip()
            processed_formula = processed_formula or len(reference_texts) != 1 or formula_body != reference_texts[0]
            for reference_sheet, reference_cell in dict.fromkeys(references):
                visit(reference_sheet, reference_cell, next_path, active | {key}, depth + 1)

        visit(sheet, cell, [], set(), 0)
        candidates = []
        for chain in chains:
            leaf = chain[-1]
            if leaf["type"] == "constant":
                candidate = {"sheet": leaf["sheet"], "cell": leaf["cell"], "reason": "formula_chain_ends_at_constant", "reference_path": chain}
                if (candidate["sheet"], candidate["cell"]) not in {(item["sheet"], item["cell"]) for item in candidates}:
                    candidates.append(candidate)
        return {
            "chains": chains,
            "candidates": candidates,
            "cycle_detected": cycle_detected,
            "max_depth_reached": max_depth_reached,
            "unparseable": unparseable,
            "read_error": read_error,
            "processed_formula": processed_formula,
            "max_depth": max((len(chain) for chain in chains), default=0),
        }


def build_formula_graph(engine: Any, indicators: list[dict[str, Any]], summary_sheet: dict[str, Any], *, max_depth: int = 25) -> FormulaGraph:
    cells: dict[tuple[str, str], Any] = {}
    active: set[tuple[str, str]] = set()

    def load(sheet: str, cell: str, depth: int) -> None:
        key = (sheet, cell.replace("$", "").upper())
        if key in cells or key in active or depth > max_depth:
            return
        active.add(key)
        try:
            value = engine.read_cell_formula_or_value(sheet, key[1])
        except Exception as exc:
            value = {READ_ERROR: str(exc)}
        cells[key] = value
        if isinstance(value, str) and value.startswith("="):
            for match in REFERENCE.finditer(value[1:]):
                reference_sheet = (match.group(1) or match.group(2) or sheet).strip()
                load(reference_sheet, f"{match.group(3)}{match.group(4)}", depth + 1)
        active.remove(key)

    for indicator in indicators:
        if indicator.get("classification") == "input":
            for cell in indicator["year_cells"].values():
                load(summary_sheet["name"], cell, 0)
    return FormulaGraph(cells)


class RuleService:
    def __init__(self, database_path: Path):
        self.connection = sqlite3.connect(database_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS input_rules (
                rule_id TEXT PRIMARY KEY, logical_rule_id TEXT NOT NULL, template_version_id INTEGER NOT NULL,
                template_fingerprint TEXT NOT NULL, rule_version INTEGER NOT NULL, indicator_key TEXT NOT NULL,
                status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rule_audit_log (
                id INTEGER PRIMARY KEY, operation_type TEXT NOT NULL, operation_time TEXT NOT NULL,
                actor TEXT NOT NULL, template_version_id INTEGER, template_fingerprint TEXT,
                rule_id TEXT, rule_version INTEGER, before_json TEXT, after_json TEXT,
                result TEXT NOT NULL, error TEXT
            );
            CREATE TABLE IF NOT EXISTS active_rule_sets (
                template_version_id INTEGER PRIMARY KEY, template_fingerprint TEXT NOT NULL,
                activated_at TEXT NOT NULL, actor TEXT NOT NULL, active INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rule_summary_index (
                rule_id TEXT PRIMARY KEY, logical_rule_id TEXT NOT NULL, template_version_id INTEGER NOT NULL,
                rule_version INTEGER NOT NULL, display_name TEXT NOT NULL, indicator_group TEXT NOT NULL,
                confirmation_status TEXT NOT NULL, confidence TEXT NOT NULL, configuration_pending INTEGER NOT NULL,
                diagnostics_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rule_summary_latest ON rule_summary_index(template_version_id, logical_rule_id, rule_version);
            CREATE INDEX IF NOT EXISTS idx_input_rules_history ON input_rules(logical_rule_id, rule_version);
            CREATE INDEX IF NOT EXISTS idx_rule_audit_rule ON rule_audit_log(rule_id, id);
            CREATE TABLE IF NOT EXISTS rule_review_cache (
                rule_id TEXT PRIMARY KEY, review_json TEXT NOT NULL
            );
        """)

    @staticmethod
    def _indicator_key(indicator: dict[str, Any]) -> str:
        return f'{indicator["group"]}|{indicator["display_name"]}|{indicator["row"]}'

    @staticmethod
    def _adjustment(indicator: dict[str, Any]) -> dict[str, Any]:
        group, unit = indicator["group"], indicator.get("unit", "未知")
        if group == "规模假设":
            return {"display_unit": "亿元", "adjustment_mode": "absolute", "minimum_step": 1, "allowed_range": None, "linkage_strategy": "independent", "configuration_pending": True}
        if group == "价格假设" and unit == "%":
            return {"display_unit": "%", "adjustment_mode": "percentage_point", "minimum_step": 0.01, "allowed_range": None, "linkage_strategy": "independent", "configuration_pending": True}
        return {"adjustment_mode": None, "minimum_step": None, "allowed_range": None, "linkage_strategy": None, "configuration_pending": True}

    def _audit(self, operation: str, actor: str, after: dict[str, Any] | None, before: dict[str, Any] | None = None, result: str = "success", error: str | None = None) -> None:
        source = after or before or {}
        def audit_value(value: dict[str, Any] | None) -> str | None:
            if not value:
                return None
            compact = {key: item for key, item in value.items() if key not in {"formula_dependency_chain", "candidate_source_cells"}}
            if "candidate_source_cells" in value:
                compact["candidate_source_cell_count"] = len(value["candidate_source_cells"])
            return json.dumps(compact, ensure_ascii=False)
        self.connection.execute(
            "INSERT INTO rule_audit_log(operation_type, operation_time, actor, template_version_id, template_fingerprint, rule_id, rule_version, before_json, after_json, result, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (operation, _now(), actor, source.get("template_version_id"), source.get("template_fingerprint"), source.get("rule_id"), source.get("rule_version"), audit_value(before), audit_value(after), result, error),
        )
        self.connection.commit()

    def _save(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.connection.execute(
                "INSERT INTO input_rules VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (payload["rule_id"], payload["logical_rule_id"], payload["template_version_id"], payload["template_fingerprint"], payload["rule_version"], payload["indicator_key"], payload["confirmation_status"], json.dumps(payload, ensure_ascii=False), payload["created_at"]),
            )
            self._index_rule(payload)
            self._cache_rule_review(payload)
            self.connection.commit()
        return payload

    def _index_rule(self, payload: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO rule_summary_index VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (payload["rule_id"], payload["logical_rule_id"], payload["template_version_id"], payload["rule_version"], payload["display_name"], payload["indicator_group"], payload["confirmation_status"], payload["confidence"], int(bool(payload.get("configuration_pending"))), json.dumps(payload.get("discovery_diagnostics", {}), ensure_ascii=False), payload["created_at"]),
        )

    def _cache_rule_review(self, payload: dict[str, Any], chain_limit: int = 20) -> None:
        chains = payload.get("formula_dependency_chain", {})
        review = {key: value for key, value in payload.items() if key not in {"formula_dependency_chain", "candidate_source_cells"}}
        review["candidate_source_cells"] = [{key: value for key, value in candidate.items() if key != "reference_paths"} for candidate in payload.get("candidate_source_cells", [])]
        review["formula_dependency_chain"] = {year: year_chains[:chain_limit] for year, year_chains in chains.items()}
        review["formula_chain_counts"] = {year: len(year_chains) for year, year_chains in chains.items()}
        self.connection.execute("INSERT OR REPLACE INTO rule_review_cache VALUES (?, ?)", (payload["rule_id"], json.dumps(review, ensure_ascii=False)))

    def discover_rules(self, template_version_id: int, template_fingerprint: str, summary_sheet: dict[str, Any], indicators: list[dict[str, Any]], graph: FormulaGraph, actor: str) -> list[dict[str, Any]]:
        results = []
        for indicator in indicators:
            if indicator.get("classification") != "input":
                continue
            key = self._indicator_key(indicator)
            year_traces = {str(year): graph.trace(summary_sheet["name"], indicator["year_cells"][str(year)]) for year in YEARS}
            grouped: dict[tuple[str, int], dict[str, Any]] = {}
            for year, trace in year_traces.items():
                sheet_positions: dict[str, int] = {}
                for candidate in trace["candidates"]:
                    position = sheet_positions.get(candidate["sheet"], 0)
                    sheet_positions[candidate["sheet"]] = position + 1
                    group_key = (candidate["sheet"], position)
                    item = grouped.setdefault(group_key, {"sheet": candidate["sheet"], "year_cells": {}, "reason": candidate["reason"], "reference_paths": {}})
                    item["year_cells"][year] = candidate["cell"]
                    item["reference_paths"].setdefault(year, []).append(candidate["reference_path"])
            candidates = list(grouped.values())
            diagnostic = {
                "cycle_detected": any(trace["cycle_detected"] for trace in year_traces.values()),
                "unparseable": any(trace["unparseable"] for trace in year_traces.values()),
                "max_depth_reached": any(trace["max_depth_reached"] for trace in year_traces.values()),
                "read_error": any(trace["read_error"] for trace in year_traces.values()),
                "processed_formula": any(trace["processed_formula"] for trace in year_traces.values()),
                "max_depth": max(trace["max_depth"] for trace in year_traces.values()),
            }
            complete_candidates = [item for item in candidates if len(item["year_cells"]) == len(YEARS)]
            has_diagnostic_error = diagnostic["cycle_detected"] or diagnostic["unparseable"] or diagnostic["max_depth_reached"] or diagnostic["read_error"] or diagnostic["processed_formula"]
            confidence = "high" if len(complete_candidates) == 1 and not has_diagnostic_error else "low"
            status = "unsupported" if diagnostic["unparseable"] and not candidates else "pending_confirmation"
            previous = self._latest_confirmed(key)
            comparable = self._comparison(indicator, summary_sheet, candidates, year_traces)
            reused_from = None
            confirmed_sources = []
            adjustment = self._adjustment(indicator)
            reused_from_rule_id = None
            if previous:
                previous_comparable = self._comparison_from_rule(previous)
                if previous["template_fingerprint"] == template_fingerprint and previous_comparable == comparable:
                    status, confirmed_sources, reused_from = "confirmed", previous["confirmed_source_cells"], previous["rule_id"]
                    adjustment = {name: previous.get(name) for name in ("adjustment_mode", "minimum_step", "allowed_range", "linkage_strategy", "configuration_pending")}
                elif previous_comparable != comparable:
                    status = "changed"
            now = _now()
            payload = {
                "rule_id": str(uuid.uuid4()), "logical_rule_id": previous["logical_rule_id"] if previous else str(uuid.uuid4()),
                "template_version_id": template_version_id, "template_fingerprint": template_fingerprint,
                "rule_version": self._next_version(previous["logical_rule_id"]) if previous else 1,
                "indicator_key": key, "summary_sheet": summary_sheet, "indicator_row": indicator["row"],
                "year_column_mapping": indicator["year_cells"], "indicator_group": indicator["group"],
                "display_name": indicator["display_name"], "display_unit": indicator.get("unit"),
                "classification": indicator["classification"], "display_cells": indicator["year_cells"],
                "candidate_source_cells": candidates, "confirmed_source_cells": confirmed_sources,
                "formula_dependency_chain": {year: trace["chains"] for year, trace in year_traces.items()},
                **adjustment, "confidence": confidence, "confirmation_status": status,
                "discovery_diagnostics": diagnostic, "created_at": now, "updated_at": now, "actor": actor,
                "reused_from_rule_id": reused_from,
            }
            self._save(payload)
            self._audit("rule_reused" if reused_from else "rule_changed" if status == "changed" else "rule_candidate_generated", actor, payload)
            self._audit("rule_discovered", actor, payload)
            results.append(payload)
        return results

    @staticmethod
    def _comparison(indicator: dict[str, Any], summary_sheet: dict[str, Any], candidates: list[dict[str, Any]], traces: dict[str, Any]) -> dict[str, Any]:
        return {"identity": (indicator["group"], indicator["display_name"], indicator["row"]), "summary_sheet": summary_sheet, "display_cells": indicator["year_cells"], "sources": [{"sheet": item["sheet"], "year_cells": item["year_cells"]} for item in candidates], "chains": {year: trace["chains"] for year, trace in traces.items()}}

    @staticmethod
    def _comparison_from_rule(rule: dict[str, Any]) -> dict[str, Any]:
        return {"identity": (rule["indicator_group"], rule["display_name"], rule["indicator_row"]), "summary_sheet": rule["summary_sheet"], "display_cells": rule["display_cells"], "sources": [{"sheet": item["sheet"], "year_cells": item["year_cells"]} for item in rule["candidate_source_cells"]], "chains": rule["formula_dependency_chain"]}

    def _latest_confirmed(self, indicator_key: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT payload_json FROM input_rules WHERE indicator_key = ? AND status = 'confirmed' ORDER BY rule_version DESC LIMIT 1", (indicator_key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _next_version(self, logical_rule_id: str) -> int:
        return self.connection.execute("SELECT COALESCE(MAX(rule_version), 0) + 1 FROM input_rules WHERE logical_rule_id = ?", (logical_rule_id,)).fetchone()[0]

    def _get(self, rule_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT payload_json FROM input_rules WHERE rule_id = ?", (rule_id,)).fetchone()
        if not row:
            raise KeyError(rule_id)
        return json.loads(row[0])

    def _new_version(self, rule_id: str, actor: str, operation: str, **changes: Any) -> dict[str, Any]:
        before = self._get(rule_id)
        after = dict(before)
        after.update(changes)
        after.update({"rule_id": str(uuid.uuid4()), "rule_version": self._next_version(before["logical_rule_id"]), "updated_at": _now(), "actor": actor})
        self._save(after)
        self._audit(operation, actor, after, before)
        return after

    def confirm_rule(self, rule_id: str, selected_sources: dict[str, dict[str, str]], *, actor: str) -> dict[str, Any]:
        return self._new_version(rule_id, actor, "rule_confirmed", confirmed_source_cells=[{"year": year, **source} for year, source in selected_sources.items()], confirmation_status="confirmed")

    def edit_rule(self, rule_id: str, *, actor: str, **changes: Any) -> dict[str, Any]:
        if changes.get("linkage_strategy") and changes["linkage_strategy"] not in LINKAGE_STRATEGIES:
            raise ValueError("invalid linkage strategy")
        return self._new_version(rule_id, actor, "rule_edited", **changes)

    def reject_rule(self, rule_id: str, *, actor: str) -> dict[str, Any]:
        return self._new_version(rule_id, actor, "rule_rejected", confirmation_status="rejected")

    def list_rules(self, template_version_id: int, status: str | None = None) -> list[dict[str, Any]]:
        query, params = "SELECT payload_json FROM input_rules WHERE template_version_id = ?", [template_version_id]
        if status:
            if status not in STATUSES:
                raise ValueError("invalid rule status")
            query, params = query + " AND status = ?", [template_version_id, status]
        return [json.loads(row[0]) for row in self.connection.execute(query + " ORDER BY created_at", params)]

    def list_latest_rule_summaries(self, template_version_id: int) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute("""
            SELECT rule_id, logical_rule_id, rule_version, confirmation_status, display_name,
                indicator_group, confidence, configuration_pending, diagnostics_json
            FROM rule_summary_index current
            WHERE template_version_id = ? AND rule_version = (
                SELECT MAX(newer.rule_version) FROM rule_summary_index newer
                WHERE newer.logical_rule_id = current.logical_rule_id
            ) ORDER BY created_at
            """, (template_version_id,)).fetchall()
        return [{"rule_id": row[0], "logical_rule_id": row[1], "rule_version": row[2], "confirmation_status": row[3], "display_name": row[4], "indicator_group": row[5], "confidence": row[6], "configuration_pending": bool(row[7]), "discovery_diagnostics": json.loads(row[8] or "{}") if isinstance(row[8], str) else row[8] or {}} for row in rows]

    def rebuild_summary_index(self) -> int:
        with self.lock:
            self.connection.execute("DELETE FROM rule_summary_index")
            count = 0
            for row in self.connection.execute("SELECT payload_json FROM input_rules"):
                self._index_rule(json.loads(row[0])); count += 1
            self.connection.commit()
        return count

    def rebuild_review_cache(self) -> int:
        with self.lock:
            self.connection.execute("DELETE FROM rule_review_cache")
            count = 0
            for row in self.connection.execute("SELECT payload_json FROM input_rules"):
                self._cache_rule_review(json.loads(row[0])); count += 1
            self.connection.commit()
        return count

    def get_rule(self, rule_id: str) -> dict[str, Any]:
        with self.lock:
            return self._get(rule_id)

    def get_rule_review(self, rule_id: str, chain_limit: int = 20) -> dict[str, Any]:
        with self.lock:
            row = self.connection.execute("SELECT review_json FROM rule_review_cache WHERE rule_id = ?", (rule_id,)).fetchone()
            if not row:
                payload = self._get(rule_id); self._cache_rule_review(payload, chain_limit); self.connection.commit()
                row = self.connection.execute("SELECT review_json FROM rule_review_cache WHERE rule_id = ?", (rule_id,)).fetchone()
            review = json.loads(row[0])
        if chain_limit < 20:
            review["formula_dependency_chain"] = {year: chains[:chain_limit] for year, chains in review["formula_dependency_chain"].items()}
        return review

    def get_rule_history(self, logical_rule_id: str) -> list[dict[str, Any]]:
        return [json.loads(row[0]) for row in self.connection.execute("SELECT payload_json FROM input_rules WHERE logical_rule_id = ? ORDER BY rule_version", (logical_rule_id,))]

    def get_rule_history_summaries(self, logical_rule_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute("SELECT rule_id, rule_version, status, created_at FROM input_rules WHERE logical_rule_id = ? ORDER BY rule_version", (logical_rule_id,)).fetchall()
        return [{"rule_id": row[0], "rule_version": row[1], "confirmation_status": row[2], "created_at": row[3]} for row in rows]

    def list_audit_logs(self, *, template_version_id: int | None = None, rule_id: str | None = None) -> list[dict[str, Any]]:
        query, params, clauses = "SELECT * FROM rule_audit_log", [], []
        if template_version_id is not None:
            clauses.append("template_version_id = ?"); params.append(template_version_id)
        if rule_id is not None:
            clauses.append("rule_id = ?"); params.append(rule_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self.lock:
            rows = self.connection.execute(query + " ORDER BY id", params).fetchall()
        return [dict(row) for row in rows]

    def rule_set_status(self, template_version_id: int) -> dict[str, Any]:
        latest = self.list_latest_rule_summaries(template_version_id)
        unresolved = [rule for rule in latest if rule["confirmation_status"] != "confirmed" or rule.get("configuration_pending")]
        with self.lock:
            active = self.connection.execute("SELECT active FROM active_rule_sets WHERE template_version_id = ?", (template_version_id,)).fetchone()
        return {"template_version_id": template_version_id, "active": bool(active and active[0]), "complete": bool(latest) and not unresolved, "total": len(latest), "unresolved_rules": unresolved}

    def activate_rule_set(self, template_version_id: int, template_fingerprint: str, *, actor: str) -> dict[str, Any]:
        status = self.rule_set_status(template_version_id)
        if not status["complete"]:
            raise ValueError("规则集存在未解决或配置未完成规则")
        before = {"template_version_id": template_version_id, "active": status["active"]}
        self.connection.execute("UPDATE active_rule_sets SET active = 0 WHERE active = 1")
        self.connection.execute("INSERT OR REPLACE INTO active_rule_sets VALUES (?, ?, ?, ?, 1)", (template_version_id, template_fingerprint, _now(), actor))
        self.connection.commit()
        after = {**status, "active": True, "template_fingerprint": template_fingerprint}
        self._audit("rule_set_activated", actor, after, before)
        return after

    def deactivate_rule_set(self, template_version_id: int, *, actor: str) -> dict[str, Any]:
        self.connection.execute("UPDATE active_rule_sets SET active = 0 WHERE template_version_id = ?", (template_version_id,))
        self.connection.commit()
        after = {**self.rule_set_status(template_version_id), "active": False}
        self._audit("rule_set_deactivated", actor, after)
        return after

    def audit_calculation_blocked(self, rule: dict[str, Any], *, actor: str, error: str = "pending_rule_confirmation") -> None:
        self._audit("calculation_blocked_pending_rule", actor, rule, result="blocked", error=error)

    def compact_audit_payloads(self) -> int:
        rows = self.connection.execute("SELECT id, before_json, after_json FROM rule_audit_log").fetchall()
        changed = 0
        for row in rows:
            values = []
            for raw in (row["before_json"], row["after_json"]):
                if not raw:
                    values.append(None); continue
                payload = json.loads(raw)
                payload.pop("formula_dependency_chain", None)
                candidates = payload.pop("candidate_source_cells", None)
                if candidates is not None:
                    payload["candidate_source_cell_count"] = len(candidates)
                values.append(json.dumps(payload, ensure_ascii=False))
            if values != [row["before_json"], row["after_json"]]:
                self.connection.execute("UPDATE rule_audit_log SET before_json = ?, after_json = ? WHERE id = ?", (*values, row["id"])); changed += 1
        self.connection.commit()
        return changed

    def close(self) -> None:
        self.connection.close()


LegacyRuleService = RuleService


class RuleService(RuleStore):
    def discover_rules(self, template_version_id: int, template_fingerprint: str, summary_sheet: dict[str, Any], indicators: list[dict[str, Any]], graph: FormulaGraph, actor: str) -> list[dict[str, Any]]:
        results = []
        for indicator in indicators:
            if indicator.get("classification") != "input":
                continue
            year_traces = {str(year): graph.trace(summary_sheet["name"], indicator["year_cells"][str(year)]) for year in YEARS}
            grouped: dict[tuple[str, int], dict[str, Any]] = {}
            for year, trace in year_traces.items():
                positions: dict[str, int] = {}
                for candidate in trace["candidates"]:
                    position = positions.get(candidate["sheet"], 0); positions[candidate["sheet"]] = position + 1
                    item = grouped.setdefault((candidate["sheet"], position), {"sheet": candidate["sheet"], "year_cells": {}, "reason": candidate["reason"]})
                    item["year_cells"][year] = candidate["cell"]
            diagnostics = {
                "cycle_detected": any(trace["cycle_detected"] for trace in year_traces.values()),
                "unparseable": any(trace["unparseable"] for trace in year_traces.values()),
                "max_depth_reached": any(trace["max_depth_reached"] for trace in year_traces.values()),
                "read_error": any(trace["read_error"] for trace in year_traces.values()),
                "processed_formula": any(trace["processed_formula"] for trace in year_traces.values()),
                "max_depth": max(trace["max_depth"] for trace in year_traces.values()),
            }
            candidates = list(grouped.values())
            complete = [candidate for candidate in candidates if len(candidate["year_cells"]) == len(YEARS)]
            confidence = "high" if len(complete) == 1 and not any(diagnostics[key] for key in ("cycle_detected", "unparseable", "max_depth_reached", "read_error", "processed_formula")) else "low"
            status = "unsupported" if diagnostics["unparseable"] and not candidates else "pending_confirmation"
            indicator_key = f'{indicator["group"]}|{indicator["display_name"]}|{indicator["row"]}'
            previous = self.connection.execute("SELECT logical_rule_id FROM rule_versions WHERE json_extract(identity_json, '$.indicator_key') = ? ORDER BY rule_version DESC LIMIT 1", (indicator_key,)).fetchone()
            logical_rule_id = previous[0] if previous else str(uuid.uuid4())
            latest = self.connection.execute("SELECT COALESCE(MAX(rule_version), 0) FROM rule_versions WHERE logical_rule_id = ?", (logical_rule_id,)).fetchone()[0]
            metadata = {"logical_rule_id": logical_rule_id, "template_version_id": template_version_id, "template_fingerprint": template_fingerprint, "indicator_key": indicator_key, "summary_sheet": summary_sheet, "indicator_row": indicator["row"], "display_cells": indicator["year_cells"], "indicator_group": indicator["group"], "display_name": indicator["display_name"], "display_unit": indicator.get("unit"), "classification": indicator["classification"]}
            discovery = {"candidate_source_cells": candidates, "formula_dependency_chain": {year: trace["chains"] for year, trace in year_traces.items()}, "discovery_diagnostics": diagnostics, "confidence": confidence}
            configuration = self._initial_adjustment(indicator); sources = {}; rejection_reason = None; reused_from_rule_id = None
            if previous:
                prior = self._latest(logical_rule_id); prior_rule = self.get_rule(prior["rule_id"], include_snapshot=False)
                snapshot_hash, _ = __import__("rule_store")._snapshot(discovery)
                if prior_rule["snapshot_id"] == snapshot_hash:
                    status = prior_rule["confirmation_status"]; configuration = prior_rule["configuration"]; sources = prior_rule["confirmed_source_cells"]; rejection_reason = prior_rule.get("rejection_reason")
                    reused_from_rule_id = prior_rule["rule_id"]
                else:
                    status = "changed" if status != "unsupported" else status
            result = self.create_discovered_rule(metadata, discovery, actor=actor, rule_version=latest + 1, status=status, configuration=configuration, sources=sources, rejection_reason=rejection_reason)
            result["formula_dependency_chain"] = discovery["formula_dependency_chain"]
            result["candidate_source_cells"] = discovery["candidate_source_cells"]
            result["discovery_diagnostics"] = diagnostics
            result["reused_from_rule_id"] = reused_from_rule_id
            results.append(result)
        return results

    @staticmethod
    def _initial_adjustment(indicator: dict[str, Any]) -> dict[str, Any]:
        if indicator["group"] == "规模假设":
            return {"display_unit": "亿元", "adjustment_mode": "absolute", "minimum_step": 1, "allowed_range": None, "linkage_strategy": "independent"}
        if indicator["group"] == "价格假设" and indicator.get("unit") == "%":
            return {"display_unit": "%", "adjustment_mode": "percentage_point", "minimum_step": 0.01, "allowed_range": None, "linkage_strategy": "independent"}
        return {"display_unit": indicator.get("unit"), "adjustment_mode": None, "minimum_step": None, "allowed_range": None, "linkage_strategy": None}

    def list_latest_rule_summaries(self, template_version_id: int) -> list[dict[str, Any]]:
        return self.list_latest_summaries(template_version_id)

    def confirm_rule(self, rule_id: str, selected_sources: dict[str, dict[str, str]], *, actor: str) -> dict[str, Any]:
        current = self.get_rule(rule_id, include_snapshot=False)
        configuration = current.get("configuration", {"display_unit": current.get("display_unit"), "adjustment_mode": current.get("adjustment_mode"), "minimum_step": current.get("minimum_step"), "allowed_range": current.get("allowed_range"), "linkage_strategy": current.get("linkage_strategy")})
        return self.confirm_and_configure(rule_id, expected_version=current["rule_version"], selected_sources=selected_sources, configuration=configuration, actor=actor)

    def edit_rule(self, rule_id: str, *, actor: str, expected_version: int | None = None, configuration: dict[str, Any] | None = None, **changes: Any) -> dict[str, Any]:
        current = self.get_rule(rule_id, include_snapshot=False)
        config = dict(current.get("configuration", {})); config.update(configuration or changes)
        return super().edit_rule(rule_id, expected_version=expected_version or current["rule_version"], configuration=config, actor=actor)

    def reject_rule(self, rule_id: str, *, actor: str, expected_version: int | None = None, reason: str = "不适用") -> dict[str, Any]:
        current = self.get_rule(rule_id, include_snapshot=False)
        return super().reject_rule(rule_id, expected_version=expected_version or current["rule_version"], reason=reason, actor=actor)

    def list_rules(self, template_version_id: int, status: str | None = None) -> list[dict[str, Any]]:
        rules = self.list_latest_summaries(template_version_id)
        return [rule for rule in rules if not status or rule["confirmation_status"] == status]

    def get_rule_review(self, rule_id: str, chain_limit: int = 20) -> dict[str, Any]:
        rule = self.get_rule(rule_id)
        counts = rule.get("formula_chain_counts", {})
        rule["formula_dependency_chain"] = {year: self.get_formula_chains(rule_id, year, offset=0, limit=chain_limit)["items"] for year in counts}
        rule["formula_chain_truncated"] = {year: count > 0 for year, count in counts.items()}
        return rule

    def get_rule_history_summaries(self, logical_rule_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT rule_id, rule_version, confirmation_status, created_at, updated_at, actor, rejection_reason, configuration_json, confirmed_sources_json FROM rule_versions WHERE logical_rule_id = ? ORDER BY rule_version", (logical_rule_id,)).fetchall()
        return [{"rule_id": row[0], "rule_version": row[1], "confirmation_status": row[2], "created_at": row[3], "updated_at": row[4], "actor": row[5], "rejection_reason": row[6], "configuration": json.loads(row[7]), "confirmed_source_cells": json.loads(row[8])} for row in rows]

    def get_rule_history(self, logical_rule_id: str) -> list[dict[str, Any]]:
        return self.get_rule_history_summaries(logical_rule_id)

    def list_audit_logs(self, *, logical_rule_id: str | None = None, template_version_id: int | None = None, limit: int = 100, offset: int = 0, **_: Any) -> list[dict[str, Any]]:
        query, params, clauses = "SELECT * FROM rule_audit_log", [], []
        if logical_rule_id:
            clauses.append("logical_rule_id = ?"); params.append(logical_rule_id)
        if template_version_id is not None:
            clauses.append("template_version_id = ?"); params.append(template_version_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        params.extend([limit, offset])
        return [dict(row) for row in self.connection.execute(query + " ORDER BY id DESC LIMIT ? OFFSET ?", params)]

    def rule_set_status(self, template_version_id: int) -> dict[str, Any]:
        rules = self.list_latest_summaries(template_version_id)
        unresolved = [rule for rule in rules if rule["confirmation_status"] != "confirmed" or rule["configuration_pending"]]
        publication = self.get_active_publication(template_version_id)
        return {"template_version_id": template_version_id, "active": bool(publication), "publication": publication, "complete": bool(rules) and not unresolved, "total": len(rules), "configuration_incomplete": sum(rule["configuration_pending"] for rule in rules), "unresolved_rules": unresolved}

    def activate_rule_set(self, template_version_id: int, template_fingerprint: str, *, actor: str) -> dict[str, Any]:
        return self.publish(template_version_id, template_fingerprint, actor=actor)

    def deactivate_rule_set(self, template_version_id: int, *, actor: str) -> dict[str, Any]:
        try:
            self._transaction()
            publication = self.get_active_publication(template_version_id)
            if publication:
                self.connection.execute("UPDATE rule_publications SET active = 0 WHERE publication_id = ?", (publication["publication_id"],))
                members = self.get_active_publication_rules(template_version_id)
                if members:
                    self._audit("rule_set_deactivated", actor, {**members[0], "publication_id": publication["publication_id"]})
            self.connection.commit()
            return {"template_version_id": template_version_id, "active": False, "actor": actor}
        except Exception:
            self.connection.rollback(); raise
