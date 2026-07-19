"""命名场景持久化：SQLite 存储场景记录与场景审计日志。

场景只保存相对基准的输入调整（input_adjustments）与最近一次计算结果快照，
不保存整本工作簿状态；历史模板场景的只读判定由服务层按活动模板指纹动态计算。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCENARIO_TYPES = ("baseline", "optimistic", "pessimistic", "custom", "reverse_result")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ScenarioStore:
    def __init__(self, database_path: Path | str):
        self.database_path = str(database_path)
        self.connection = sqlite3.connect(self.database_path, timeout=30, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS scenarios (
                scenario_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                scenario_type TEXT NOT NULL,
                template_version_id INTEGER NOT NULL,
                template_fingerprint TEXT NOT NULL,
                rule_publication_id TEXT,
                input_adjustments_json TEXT NOT NULL,
                result_snapshot_json TEXT,
                validation_state TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scenario_audit_log (
                id INTEGER PRIMARY KEY,
                scenario_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                actor TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                before_json TEXT,
                after_json TEXT,
                detail TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_scenario_audit ON scenario_audit_log(scenario_id, id);
        """)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "scenario_id": row["scenario_id"], "name": row["name"], "scenario_type": row["scenario_type"],
            "template_version_id": row["template_version_id"], "template_fingerprint": row["template_fingerprint"],
            "rule_publication_id": row["rule_publication_id"],
            "input_adjustments": json.loads(row["input_adjustments_json"]),
            "calculation_result_snapshot": json.loads(row["result_snapshot_json"]) if row["result_snapshot_json"] else None,
            "validation_state": row["validation_state"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def create(self, *, name: str, scenario_type: str, template_version_id: int, template_fingerprint: str, rule_publication_id: str | None, input_adjustments: dict[str, Any], calculation_result_snapshot: dict[str, Any] | None, validation_state: str | None) -> dict[str, Any]:
        if scenario_type not in SCENARIO_TYPES:
            raise ValueError(f"未知场景类型: {scenario_type}")
        scenario_id = str(uuid.uuid4())
        timestamp = _now()
        with self._lock:
            self.connection.execute(
                "INSERT INTO scenarios VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (scenario_id, name, scenario_type, template_version_id, template_fingerprint, rule_publication_id, _json(input_adjustments), _json(calculation_result_snapshot) if calculation_result_snapshot is not None else None, validation_state, timestamp, timestamp),
            )
            self.connection.commit()
        return self.get(scenario_id)

    def get(self, scenario_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM scenarios WHERE scenario_id = ?", (scenario_id,)).fetchone()
        if not row:
            raise KeyError(scenario_id)
        return self._row_to_record(row)

    def list(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM scenarios ORDER BY created_at DESC").fetchall()
        summaries = []
        for row in rows:
            record = self._row_to_record(row)
            record["adjustment_count"] = len(record.pop("input_adjustments"))
            record["has_result"] = record.pop("calculation_result_snapshot") is not None
            summaries.append(record)
        return summaries

    def rename(self, scenario_id: str, name: str) -> dict[str, Any]:
        with self._lock:
            self.connection.execute("UPDATE scenarios SET name = ?, updated_at = ? WHERE scenario_id = ?", (name, _now(), scenario_id))
            self.connection.commit()
        return self.get(scenario_id)

    def update_result(self, scenario_id: str, *, calculation_result_snapshot: dict[str, Any] | None, validation_state: str | None, rule_publication_id: str | None) -> dict[str, Any]:
        with self._lock:
            self.connection.execute(
                "UPDATE scenarios SET result_snapshot_json = ?, validation_state = ?, rule_publication_id = ?, updated_at = ? WHERE scenario_id = ?",
                (_json(calculation_result_snapshot) if calculation_result_snapshot is not None else None, validation_state, rule_publication_id, _now(), scenario_id),
            )
            self.connection.commit()
        return self.get(scenario_id)

    def delete(self, scenario_id: str) -> None:
        with self._lock:
            self.connection.execute("DELETE FROM scenarios WHERE scenario_id = ?", (scenario_id,))
            self.connection.commit()

    def audit(self, scenario_id: str, operation: str, *, actor: str, before: dict[str, Any] | None = None, after: dict[str, Any] | None = None, detail: str | None = None) -> None:
        with self._lock:
            self.connection.execute(
                "INSERT INTO scenario_audit_log(scenario_id, operation, actor, occurred_at, before_json, after_json, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (scenario_id, operation, actor, _now(), _json(before) if before is not None else None, _json(after) if after is not None else None, detail),
            )
            self.connection.commit()

    def list_audit(self, scenario_id: str | None = None) -> list[dict[str, Any]]:
        if scenario_id:
            rows = self.connection.execute("SELECT * FROM scenario_audit_log WHERE scenario_id = ? ORDER BY id", (scenario_id,)).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM scenario_audit_log ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self.connection.close()
