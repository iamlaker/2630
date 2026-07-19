from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forecast_engine import WorkbookEngine


INPUT_GROUPS = {"重要参数", "规模假设", "价格假设", "中收假设"}


class TemplateImportService:
    def __init__(self, storage_dir: Path, database_path: Path, engine: WorkbookEngine):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.engine = engine
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS template_versions (
                id INTEGER PRIMARY KEY, version INTEGER NOT NULL, fingerprint TEXT UNIQUE NOT NULL,
                filename TEXT NOT NULL, storage_id TEXT NOT NULL, file_size INTEGER NOT NULL,
                imported_at TEXT NOT NULL, import_status TEXT NOT NULL, worksheets_json TEXT NOT NULL,
                catalog_status TEXT NOT NULL, catalog_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY, operation TEXT NOT NULL, occurred_at TEXT NOT NULL,
                fingerprint TEXT, template_version INTEGER, status TEXT NOT NULL, error TEXT
            );
        """)

    @staticmethod
    def _fingerprint(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _audit(self, operation: str, fingerprint: str | None, version: int | None, status: str, error: str | None = None) -> None:
        self.connection.execute(
            "INSERT INTO audit_log(operation, occurred_at, fingerprint, template_version, status, error) VALUES (?, ?, ?, ?, ?, ?)",
            (operation, datetime.now(timezone.utc).isoformat(), fingerprint, version, status, error),
        )
        self.connection.commit()

    @staticmethod
    def _classify(item: dict[str, Any], input_overrides: dict[str, str]) -> dict[str, Any]:
        group = item["group"]
        if item["display_name"] in input_overrides:
            group = input_overrides[item["display_name"]]
        if group in INPUT_GROUPS:
            classification = "input"
        elif group == "财务结果" or item["row"] < 51:
            classification = "output"
            group = "财务结果"
        else:
            classification = "unknown"
        return {**item, "group": group, "classification": classification}

    def import_template(self, source_path: Path, *, input_overrides: dict[str, str] | None = None) -> dict[str, Any]:
        source_path = Path(source_path)
        input_overrides = {"并表口径总资产": "规模假设", **(input_overrides or {})}
        invalid_groups = set(input_overrides.values()) - INPUT_GROUPS
        if invalid_groups:
            raise ValueError(f"输入覆盖分组无效: {sorted(invalid_groups)}")
        fingerprint = None
        version = None
        try:
            if source_path.suffix.lower() != ".xlsx":
                raise ValueError("仅支持 .xlsx 模板文件")
            if not source_path.is_file():
                raise FileNotFoundError(f"模板文件不存在: {source_path}")
            fingerprint = self._fingerprint(source_path)
            existing = self.connection.execute("SELECT * FROM template_versions WHERE fingerprint = ?", (fingerprint,)).fetchone()
            if existing:
                self._audit("template_import_succeeded", fingerprint, existing["version"], "reused")
                return self._row_result(existing)
            version = self.connection.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM template_versions").fetchone()[0]
            storage_id = f"{fingerprint}.xlsx"
            stored_path = self.storage_dir / storage_id
            shutil.copy2(source_path, stored_path)
            self.engine.open_isolated(stored_path)
            workbook = self.engine.inspect_workbook()
            catalog = self.engine.read_indicator_catalog()
            catalog["indicators"] = [self._classify(item, input_overrides) for item in catalog["indicators"]]
            imported_at = datetime.now(timezone.utc).isoformat()
            cursor = self.connection.execute(
                "INSERT INTO template_versions(version, fingerprint, filename, storage_id, file_size, imported_at, import_status, worksheets_json, catalog_status, catalog_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (version, fingerprint, source_path.name, storage_id, source_path.stat().st_size, imported_at, "success", json.dumps(workbook["worksheets"], ensure_ascii=False), "generated", json.dumps(catalog, ensure_ascii=False)),
            )
            self.connection.commit()
            self._audit("template_import_succeeded", fingerprint, version, "success")
            self._audit("catalog_generated", fingerprint, version, "success")
            row = self.connection.execute("SELECT * FROM template_versions WHERE id = ?", (cursor.lastrowid,)).fetchone()
            return self._row_result(row)
        except Exception as exc:
            self._audit("template_import_failed", fingerprint, version, "failed", str(exc))
            return {"template_version": version, "template_version_id": None, "template_fingerprint": fingerprint, "worksheet": None, "worksheets": [], "indicator_catalog": [], "year_mapping": {}, "import_status": "failed", "error": str(exc)}
        finally:
            try:
                self.engine.close()
            except Exception:
                pass

    @staticmethod
    def _row_result(row: sqlite3.Row) -> dict[str, Any]:
        catalog = json.loads(row["catalog_json"])
        return {
            "template_version": row["version"], "template_version_id": row["id"],
            "template_fingerprint": row["fingerprint"], "filename": row["filename"],
            "storage_id": row["storage_id"], "file_size": row["file_size"],
            "imported_at": row["imported_at"], "worksheets": json.loads(row["worksheets_json"]),
            "worksheet": catalog["worksheet"], "indicator_catalog": catalog["indicators"],
            "year_mapping": catalog["year_mapping"], "catalog_status": row["catalog_status"],
            "import_status": row["import_status"], "error": None,
        }

    def list_template_versions(self) -> list[dict[str, Any]]:
        return [self._row_result(row) for row in self.connection.execute("SELECT * FROM template_versions ORDER BY version")]

    def get_indicator_catalog(self, template_version_id: int) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM template_versions WHERE id = ?", (template_version_id,)).fetchone()
        return self._row_result(row) if row else None

    def list_audit_events(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM audit_log ORDER BY id")]

    def close(self) -> None:
        self.connection.close()
