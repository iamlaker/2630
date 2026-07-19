from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _snapshot(discovery: dict[str, Any]) -> tuple[str, str]:
    payload = _json(discovery)
    return hashlib.sha256(payload.encode()).hexdigest(), payload


class RuleStore:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.connection = sqlite3.connect(self.database_path, timeout=30, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS discovery_snapshots (
                snapshot_id TEXT PRIMARY KEY, snapshot_hash TEXT UNIQUE NOT NULL,
                content_json TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS formula_chain_pages (
                snapshot_id TEXT NOT NULL, year TEXT NOT NULL, page_number INTEGER NOT NULL,
                chain_count INTEGER NOT NULL, content_json TEXT NOT NULL,
                PRIMARY KEY(snapshot_id, year, page_number),
                FOREIGN KEY(snapshot_id) REFERENCES discovery_snapshots(snapshot_id)
            );
            CREATE TABLE IF NOT EXISTS rule_versions (
                rule_id TEXT PRIMARY KEY, logical_rule_id TEXT NOT NULL,
                template_version_id INTEGER NOT NULL, template_fingerprint TEXT NOT NULL,
                rule_version INTEGER NOT NULL, snapshot_id TEXT NOT NULL,
                identity_json TEXT NOT NULL, confirmation_status TEXT NOT NULL,
                configuration_json TEXT NOT NULL, confirmed_sources_json TEXT NOT NULL,
                rejection_reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                actor TEXT NOT NULL, legacy_rule_version INTEGER,
                UNIQUE(logical_rule_id, rule_version),
                FOREIGN KEY(snapshot_id) REFERENCES discovery_snapshots(snapshot_id)
            );
            CREATE INDEX IF NOT EXISTS idx_rule_versions_template ON rule_versions(template_version_id, logical_rule_id, rule_version);
            CREATE TABLE IF NOT EXISTS rule_audit_log (
                id INTEGER PRIMARY KEY, operation_type TEXT NOT NULL, operation_time TEXT NOT NULL,
                actor TEXT NOT NULL, template_version_id INTEGER NOT NULL,
                template_fingerprint TEXT NOT NULL, logical_rule_id TEXT NOT NULL,
                rule_id TEXT NOT NULL, rule_version INTEGER NOT NULL,
                before_json TEXT, after_json TEXT, result TEXT NOT NULL, error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_audit_logical ON rule_audit_log(logical_rule_id, id);
            CREATE TABLE IF NOT EXISTS rule_publications (
                publication_id TEXT PRIMARY KEY, template_version_id INTEGER NOT NULL,
                template_fingerprint TEXT NOT NULL, published_at TEXT NOT NULL,
                actor TEXT NOT NULL, active INTEGER NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_active_publication_per_template
                ON rule_publications(template_version_id) WHERE active = 1;
            CREATE TABLE IF NOT EXISTS publication_members (
                publication_id TEXT NOT NULL, logical_rule_id TEXT NOT NULL,
                rule_id TEXT NOT NULL, rule_version INTEGER NOT NULL,
                PRIMARY KEY(publication_id, logical_rule_id),
                FOREIGN KEY(publication_id) REFERENCES rule_publications(publication_id),
                FOREIGN KEY(rule_id) REFERENCES rule_versions(rule_id)
            );
            CREATE TABLE IF NOT EXISTS legacy_rule_map (
                legacy_rule_id TEXT PRIMARY KEY, rule_id TEXT NOT NULL,
                legacy_rule_version INTEGER, migrated_rule_version INTEGER NOT NULL,
                snapshot_hash TEXT NOT NULL
            );
        """)

    def _transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _identity(metadata: dict[str, Any]) -> dict[str, Any]:
        excluded = {"logical_rule_id", "template_version_id", "template_fingerprint"}
        return {key: value for key, value in metadata.items() if key not in excluded}

    def _insert_snapshot(self, discovery: dict[str, Any]) -> str:
        snapshot_hash, content = _snapshot(discovery)
        existing = self.connection.execute("SELECT 1 FROM discovery_snapshots WHERE snapshot_id = ?", (snapshot_hash,)).fetchone()
        if existing:
            return snapshot_hash
        chains = discovery.get("formula_dependency_chain", {})
        light = {key: value for key, value in discovery.items() if key != "formula_dependency_chain"}
        light["formula_chain_counts"] = {str(year): len(items) for year, items in chains.items()}
        self.connection.execute("INSERT INTO discovery_snapshots VALUES (?, ?, ?, ?)", (snapshot_hash, snapshot_hash, _json(light), _now()))
        page_size = 20
        for year, items in chains.items():
            for page_number, offset in enumerate(range(0, len(items), page_size)):
                page = items[offset:offset + page_size]
                self.connection.execute("INSERT INTO formula_chain_pages VALUES (?, ?, ?, ?, ?)", (snapshot_hash, str(year), page_number, len(page), _json(page)))
        return snapshot_hash

    def _audit(self, operation: str, actor: str, rule: dict[str, Any], before: dict[str, Any] | None = None) -> None:
        compact = lambda value: None if value is None else _json({key: item for key, item in value.items() if key not in {"formula_dependency_chain", "candidate_source_cells"}})
        self.connection.execute(
            "INSERT INTO rule_audit_log(operation_type, operation_time, actor, template_version_id, template_fingerprint, logical_rule_id, rule_id, rule_version, before_json, after_json, result, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', NULL)",
            (operation, _now(), actor, rule["template_version_id"], rule["template_fingerprint"], rule["logical_rule_id"], rule["rule_id"], rule["rule_version"], compact(before), compact(rule)),
        )

    def _insert_version(self, *, rule_id: str, metadata: dict[str, Any], rule_version: int, snapshot_id: str, status: str, configuration: dict[str, Any], sources: dict[str, dict[str, str]], actor: str, rejection_reason: str | None = None, created_at: str | None = None, legacy_rule_version: int | None = None) -> dict[str, Any]:
        timestamp = created_at or _now()
        self.connection.execute(
            "INSERT INTO rule_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rule_id, metadata["logical_rule_id"], metadata["template_version_id"], metadata["template_fingerprint"], rule_version, snapshot_id, _json(self._identity(metadata)), status, _json(configuration), _json(sources), rejection_reason, timestamp, _now(), actor, legacy_rule_version),
        )
        return self.get_rule(rule_id, include_snapshot=False)

    def create_discovered_rule(self, metadata: dict[str, Any], discovery: dict[str, Any], *, actor: str, rule_id: str | None = None, rule_version: int = 1, status: str = "pending_confirmation", configuration: dict[str, Any] | None = None, sources: dict[str, dict[str, str]] | None = None, rejection_reason: str | None = None, created_at: str | None = None, legacy_rule_version: int | None = None) -> dict[str, Any]:
        try:
            self._transaction()
            snapshot_id = self._insert_snapshot(discovery)
            rule = self._insert_version(rule_id=rule_id or str(uuid.uuid4()), metadata=metadata, rule_version=rule_version, snapshot_id=snapshot_id, status=status, configuration=configuration or {}, sources=sources or {}, actor=actor, rejection_reason=rejection_reason, created_at=created_at, legacy_rule_version=legacy_rule_version)
            self._audit("rule_discovered", actor, rule)
            self.connection.commit()
            return self.get_rule(rule["rule_id"])
        except Exception:
            self.connection.rollback()
            raise

    def _latest(self, logical_rule_id: str) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM rule_versions WHERE logical_rule_id = ? ORDER BY rule_version DESC LIMIT 1", (logical_rule_id,)).fetchone()
        if not row:
            raise KeyError(logical_rule_id)
        return row

    def _change(self, rule_id: str, *, expected_version: int, actor: str, status: str | None = None, configuration: dict[str, Any] | None = None, selected_sources: dict[str, dict[str, str]] | None = None, rejection_reason: str | None = None, operation: str) -> dict[str, Any]:
        try:
            self._transaction()
            before = self.get_rule(rule_id, include_snapshot=False)
            latest = self._latest(before["logical_rule_id"])
            if latest["rule_id"] != rule_id or latest["rule_version"] != expected_version:
                raise ValueError("规则版本冲突，请刷新后重试")
            next_version = expected_version + 1
            next_rule = self._insert_version(
                rule_id=str(uuid.uuid4()), metadata=before, rule_version=next_version,
                snapshot_id=before["snapshot_id"], status=status or before["confirmation_status"],
                configuration=configuration if configuration is not None else before["configuration"],
                sources=selected_sources if selected_sources is not None else before["confirmed_source_cells"],
                actor=actor, rejection_reason=rejection_reason if rejection_reason is not None else before.get("rejection_reason"),
            )
            self._audit(operation, actor, next_rule, before)
            self.connection.commit()
            return self.get_rule(next_rule["rule_id"])
        except Exception:
            self.connection.rollback()
            raise

    def edit_rule(self, rule_id: str, *, expected_version: int, configuration: dict[str, Any], actor: str) -> dict[str, Any]:
        return self._change(rule_id, expected_version=expected_version, configuration=configuration, actor=actor, operation="rule_edited")

    def confirm_and_configure(self, rule_id: str, *, expected_version: int, selected_sources: dict[str, dict[str, str]], configuration: dict[str, Any], actor: str) -> dict[str, Any]:
        return self._change(rule_id, expected_version=expected_version, status="confirmed", configuration=configuration, selected_sources=selected_sources, rejection_reason=None, actor=actor, operation="rule_confirmed")

    def reject_rule(self, rule_id: str, *, expected_version: int, reason: str, actor: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("拒绝理由不能为空")
        return self._change(rule_id, expected_version=expected_version, status="rejected", rejection_reason=reason.strip(), actor=actor, operation="rule_rejected")

    def get_rule(self, rule_id: str, *, include_snapshot: bool = True) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM rule_versions WHERE rule_id = ?", (rule_id,)).fetchone()
        if not row:
            raise KeyError(rule_id)
        identity = json.loads(row["identity_json"])
        result = {**identity, "rule_id": row["rule_id"], "logical_rule_id": row["logical_rule_id"], "template_version_id": row["template_version_id"], "template_fingerprint": row["template_fingerprint"], "rule_version": row["rule_version"], "snapshot_id": row["snapshot_id"], "snapshot_hash": row["snapshot_id"], "confirmation_status": row["confirmation_status"], "configuration": json.loads(row["configuration_json"]), "confirmed_source_cells": json.loads(row["confirmed_sources_json"]), "rejection_reason": row["rejection_reason"], "created_at": row["created_at"], "updated_at": row["updated_at"], "actor": row["actor"]}
        result.update(result["configuration"])
        result["configuration_pending"] = not all(result["configuration"].get(key) not in (None, "") for key in ("adjustment_mode", "linkage_strategy"))
        if include_snapshot:
            snapshot = self.connection.execute("SELECT content_json FROM discovery_snapshots WHERE snapshot_id = ?", (row["snapshot_id"],)).fetchone()
            result.update(json.loads(snapshot[0]))
        return result

    def list_latest_summaries(self, template_version_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute("""
            SELECT r.* FROM rule_versions r
            JOIN (SELECT logical_rule_id, MAX(rule_version) version FROM rule_versions WHERE template_version_id = ? GROUP BY logical_rule_id) latest
              ON latest.logical_rule_id = r.logical_rule_id AND latest.version = r.rule_version
            WHERE r.template_version_id = ? ORDER BY r.logical_rule_id
        """, (template_version_id, template_version_id)).fetchall()
        return [self.get_rule(row["rule_id"], include_snapshot=False) for row in rows]

    def get_formula_chains(self, rule_id: str, year: str, *, offset: int = 0, limit: int = 20) -> dict[str, Any]:
        rule = self.get_rule(rule_id, include_snapshot=False)
        snapshot = self.connection.execute("SELECT content_json FROM discovery_snapshots WHERE snapshot_id = ?", (rule["snapshot_id"],)).fetchone()
        total = json.loads(snapshot[0]).get("formula_chain_counts", {}).get(str(year), 0)
        first_page, last_page = offset // 20, (offset + limit - 1) // 20
        rows = self.connection.execute("SELECT content_json FROM formula_chain_pages WHERE snapshot_id = ? AND year = ? AND page_number BETWEEN ? AND ? ORDER BY page_number", (rule["snapshot_id"], str(year), first_page, last_page)).fetchall()
        combined = [item for row in rows for item in json.loads(row[0])]
        start = offset - first_page * 20
        items = combined[start:start + limit]
        return {"year": str(year), "offset": offset, "limit": limit, "total": total, "items": items, "truncated": offset + len(items) < total}

    def get_version_diff(self, rule_id: str) -> dict[str, Any]:
        current = self.get_rule(rule_id, include_snapshot=False)
        previous_row = self.connection.execute("SELECT rule_id FROM rule_versions WHERE logical_rule_id = ? AND rule_version < ? ORDER BY rule_version DESC LIMIT 1", (current["logical_rule_id"], current["rule_version"])).fetchone()
        if not previous_row:
            return {}
        previous = self.get_rule(previous_row[0], include_snapshot=False)
        fields = ("confirmation_status", "configuration", "confirmed_source_cells", "rejection_reason", "snapshot_id")
        return {field: {"before": previous.get(field), "after": current.get(field)} for field in fields if previous.get(field) != current.get(field)}

    def publish(self, template_version_id: int, template_fingerprint: str, *, actor: str) -> dict[str, Any]:
        try:
            self._transaction()
            members = self.list_latest_summaries(template_version_id)
            unresolved = [rule for rule in members if rule["confirmation_status"] != "confirmed" or rule["configuration_pending"]]
            if not members or unresolved:
                raise ValueError("规则集存在未解决或配置未完成规则")
            self.connection.execute("UPDATE rule_publications SET active = 0 WHERE template_version_id = ? AND active = 1", (template_version_id,))
            publication_id = str(uuid.uuid4())
            self.connection.execute("INSERT INTO rule_publications VALUES (?, ?, ?, ?, ?, 1)", (publication_id, template_version_id, template_fingerprint, _now(), actor))
            self.connection.executemany("INSERT INTO publication_members VALUES (?, ?, ?, ?)", [(publication_id, rule["logical_rule_id"], rule["rule_id"], rule["rule_version"]) for rule in members])
            anchor = members[0]
            self._audit("rule_set_activated", actor, {**anchor, "publication_id": publication_id}, None)
            self.connection.commit()
            return self.get_active_publication(template_version_id)
        except Exception:
            self.connection.rollback()
            raise

    def get_active_publication(self, template_version_id: int) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM rule_publications WHERE template_version_id = ? AND active = 1", (template_version_id,)).fetchone()
        return dict(row) if row else None

    def get_active_publication_rules(self, template_version_id: int) -> list[dict[str, Any]]:
        publication = self.get_active_publication(template_version_id)
        if not publication:
            return []
        rows = self.connection.execute("SELECT rule_id FROM publication_members WHERE publication_id = ? ORDER BY logical_rule_id", (publication["publication_id"],)).fetchall()
        return [self.get_rule(row[0], include_snapshot=False) for row in rows]

    def classify_migration(self, source_template_version_id: int, target_template_version_id: int) -> dict[str, list[dict[str, Any]]]:
        source = {rule.get("indicator_key", rule["logical_rule_id"]): rule for rule in self.list_latest_summaries(source_template_version_id)}
        target = {rule.get("indicator_key", rule["logical_rule_id"]): rule for rule in self.list_latest_summaries(target_template_version_id)}
        reusable, changed, new, historical = [], [], [], []
        for identity, candidate in target.items():
            prior = source.get(identity)
            if not prior:
                new.append(candidate)
            elif prior.get("snapshot_id") == candidate.get("snapshot_id"):
                reusable.append(candidate)
            else:
                changed.append(candidate)
        for identity, prior in source.items():
            if identity not in target:
                historical.append(prior)
        return {"reusable": reusable, "changed": changed, "new": new, "historical": historical}

    def close(self) -> None:
        self.connection.close()


def migrate_legacy_database(source_path: Path, target_path: Path) -> dict[str, Any]:
    source = sqlite3.connect(f"file:{Path(source_path).resolve()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = RuleStore(target_path)
    migrated = 0
    snapshot_hashes: dict[str, str] = {}
    next_versions: dict[str, int] = {}
    try:
        for row in source.execute("SELECT * FROM input_rules ORDER BY created_at, rowid"):
            payload = json.loads(row["payload_json"])
            discovery = {key: payload.get(key) for key in ("candidate_source_cells", "formula_dependency_chain", "discovery_diagnostics", "confidence")}
            logical = row["logical_rule_id"]
            version = next_versions.get(logical, 0) + 1
            next_versions[logical] = version
            metadata = {key: payload.get(key) for key in ("indicator_key", "summary_sheet", "indicator_row", "display_cells", "indicator_group", "display_name", "display_unit", "classification")}
            metadata.update({"logical_rule_id": logical, "template_version_id": row["template_version_id"], "template_fingerprint": row["template_fingerprint"]})
            configuration = {key: payload.get(key) for key in ("display_unit", "adjustment_mode", "minimum_step", "allowed_range", "linkage_strategy")}
            sources = payload.get("confirmed_source_cells") or {}
            if isinstance(sources, list):
                sources = {str(item["year"]): {"sheet": item["sheet"], "cell": item["cell"]} for item in sources}
            migrated_rule = target.create_discovered_rule(metadata, discovery, actor=payload.get("actor") or "migration", rule_id=row["rule_id"], rule_version=version, status=payload.get("confirmation_status") or row["status"], configuration=configuration, sources=sources, rejection_reason=payload.get("rejection_reason"), created_at=row["created_at"], legacy_rule_version=row["rule_version"])
            target.connection.execute("INSERT INTO legacy_rule_map VALUES (?, ?, ?, ?, ?)", (row["rule_id"], row["rule_id"], row["rule_version"], version, migrated_rule["snapshot_hash"]))
            target.connection.commit()
            snapshot_hashes[row["rule_id"]] = migrated_rule["snapshot_hash"]
            migrated += 1
        target.connection.execute("DELETE FROM rule_audit_log")
        for audit in source.execute("SELECT * FROM rule_audit_log ORDER BY id"):
            rule = target.connection.execute("SELECT logical_rule_id, template_version_id, template_fingerprint, rule_version FROM rule_versions WHERE rule_id = ?", (audit["rule_id"],)).fetchone()
            if not rule:
                continue
            target.connection.execute("INSERT INTO rule_audit_log(id, operation_type, operation_time, actor, template_version_id, template_fingerprint, logical_rule_id, rule_id, rule_version, before_json, after_json, result, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (audit["id"], audit["operation_type"], audit["operation_time"], audit["actor"], rule["template_version_id"], rule["template_fingerprint"], rule["logical_rule_id"], audit["rule_id"], rule["rule_version"], audit["before_json"], audit["after_json"], audit["result"], audit["error"]))
        target.connection.commit()
        source_count = source.execute("SELECT COUNT(*) FROM input_rules").fetchone()[0]
        target_count = target.connection.execute("SELECT COUNT(*) FROM rule_versions").fetchone()[0]
        source_audits = source.execute("SELECT COUNT(*) FROM rule_audit_log").fetchone()[0]
        target_audits = target.connection.execute("SELECT COUNT(*) FROM rule_audit_log").fetchone()[0]
        verified = source_count == target_count == migrated and source_audits == target_audits and all(target.get_rule(rule_id, include_snapshot=False)["snapshot_hash"] == snapshot_hash for rule_id, snapshot_hash in snapshot_hashes.items())
        return {"verified": verified, "source_versions": source_count, "target_versions": target_count, "source_audits": source_audits, "target_audits": target_audits, "snapshots": target.connection.execute("SELECT COUNT(*) FROM discovery_snapshots").fetchone()[0], "legacy_map": target.connection.execute("SELECT COUNT(*) FROM legacy_rule_map").fetchone()[0]}
    finally:
        source.close(); target.close()


def backup_database(source_path: Path, backup_path: Path) -> dict[str, Any]:
    shutil.copy2(source_path, backup_path)
    digest_builder = hashlib.sha256()
    with backup_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest_builder.update(chunk)
    digest = digest_builder.hexdigest()
    return {"path": str(backup_path), "sha256": digest, "size": backup_path.stat().st_size}
