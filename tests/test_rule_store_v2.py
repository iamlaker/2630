import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from rule_store import RuleStore, backup_database, migrate_legacy_database


def discovery(size=1):
    chains = {str(year): [[{"sheet": "汇总展示表", "cell": f"D{year}", "type": "formula", "formula": "=参数!A1"}, {"sheet": "参数", "cell": "A1", "type": "constant", "formula": None}] for _ in range(size)] for year in range(2026, 2031)}
    return {"candidate_source_cells": [{"sheet": "参数", "year_cells": {str(year): "A1" for year in range(2026, 2031)}, "reason": "leaf"}], "formula_dependency_chain": chains, "discovery_diagnostics": {"processed_formula": size > 1}, "confidence": "low" if size > 1 else "high"}


def metadata(template=1, logical="logical-1", name="贷款利率"):
    return {"template_version_id": template, "template_fingerprint": f"fp-{template}", "logical_rule_id": logical, "indicator_key": f"价格假设|{name}|107", "summary_sheet": {"name": "汇总展示表", "index": 2}, "indicator_row": 107, "display_cells": {str(year): f"D{year}" for year in range(2026, 2031)}, "indicator_group": "价格假设", "display_name": name, "display_unit": "%", "classification": "input"}


class RuleStoreV2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "rules.sqlite3"
        self.store = RuleStore(self.path)

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def create(self, template=1, logical="logical-1", name="贷款利率", size=1):
        return self.store.create_discovered_rule(metadata(template, logical, name), discovery(size), actor="system")

    def config(self):
        return {"display_unit": "%", "adjustment_mode": "basis_point", "minimum_step": 1, "allowed_range": [0, 10], "linkage_strategy": "independent"}

    def sources(self):
        return {str(year): {"sheet": "参数", "cell": "A1"} for year in range(2026, 2031)}

    def test_large_discovery_snapshot_is_deduplicated_across_edits(self):
        first = self.create(size=200)
        before = self.path.stat().st_size
        current = first
        for index in range(10):
            current = self.store.edit_rule(current["rule_id"], expected_version=current["rule_version"], configuration={**self.config(), "minimum_step": index + 1}, actor="admin")
        snapshots = self.store.connection.execute("SELECT COUNT(*) FROM discovery_snapshots").fetchone()[0]
        versions = self.store.connection.execute("SELECT COUNT(*) FROM rule_versions").fetchone()[0]
        self.assertEqual((snapshots, versions), (1, 11))
        self.assertLess(self.path.stat().st_size - before, 300_000)

    def test_confirm_and_configure_creates_one_atomic_version(self):
        first = self.create()
        confirmed = self.store.confirm_and_configure(first["rule_id"], expected_version=1, selected_sources=self.sources(), configuration=self.config(), actor="admin")
        self.assertEqual(confirmed["rule_version"], 2)
        self.assertEqual(confirmed["confirmation_status"], "confirmed")
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM rule_versions").fetchone()[0], 2)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM rule_audit_log WHERE operation_type='rule_confirmed'").fetchone()[0], 1)

    def test_concurrent_edits_allow_one_winner_without_duplicate_versions(self):
        first = self.create()
        outcomes = []
        def edit(step):
            store = RuleStore(self.path)
            try: outcomes.append(store.edit_rule(first["rule_id"], expected_version=1, configuration={**self.config(), "minimum_step": step}, actor="admin")["rule_version"])
            except ValueError as exc: outcomes.append(str(exc))
            finally: store.close()
        threads = [threading.Thread(target=edit, args=(step,)) for step in (1, 2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(outcomes.count(2), 1)
        self.assertTrue(any("版本冲突" in str(item) for item in outcomes))
        duplicates = self.store.connection.execute("SELECT logical_rule_id, rule_version, COUNT(*) FROM rule_versions GROUP BY logical_rule_id, rule_version HAVING COUNT(*) > 1").fetchall()
        self.assertEqual(duplicates, [])

    def test_publication_freezes_members_and_templates_activate_independently(self):
        rules = []
        for template in (1, 2):
            discovered = self.create(template, f"logical-{template}", f"指标{template}")
            rules.append(self.store.confirm_and_configure(discovered["rule_id"], expected_version=1, selected_sources=self.sources(), configuration=self.config(), actor="admin"))
            self.store.publish(template, f"fp-{template}", actor="admin")
        edited = self.store.edit_rule(rules[0]["rule_id"], expected_version=2, configuration={**self.config(), "minimum_step": 9}, actor="admin")
        published = self.store.get_active_publication_rules(1)
        self.assertEqual(published[0]["rule_id"], rules[0]["rule_id"])
        self.assertNotEqual(published[0]["rule_id"], edited["rule_id"])
        self.assertTrue(self.store.get_active_publication(1)["active"])
        self.assertTrue(self.store.get_active_publication(2)["active"])

    def test_formula_chains_are_paginated_without_truncating_snapshot(self):
        first = self.create(size=55)
        page = self.store.get_formula_chains(first["rule_id"], "2026", offset=20, limit=20)
        self.assertEqual((page["total"], len(page["items"]), page["truncated"]), (55, 20, True))
        final = self.store.get_formula_chains(first["rule_id"], "2026", offset=40, limit=20)
        self.assertEqual((len(final["items"]), final["truncated"]), (15, False))

    def test_migration_preserves_ids_semantics_and_snapshot_hashes(self):
        legacy = Path(self.temp.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy)
        connection.executescript("CREATE TABLE input_rules(rule_id TEXT, logical_rule_id TEXT, template_version_id INTEGER, template_fingerprint TEXT, rule_version INTEGER, indicator_key TEXT, status TEXT, payload_json TEXT, created_at TEXT); CREATE TABLE rule_audit_log(id INTEGER PRIMARY KEY, operation_type TEXT, operation_time TEXT, actor TEXT, template_version_id INTEGER, template_fingerprint TEXT, rule_id TEXT, rule_version INTEGER, before_json TEXT, after_json TEXT, result TEXT, error TEXT);")
        payload = {**metadata(), **discovery(3), "rule_id": "old-rule", "rule_version": 1, "confirmation_status": "pending_confirmation", "confirmed_source_cells": [], "adjustment_mode": None, "minimum_step": None, "allowed_range": None, "linkage_strategy": None, "configuration_pending": True, "created_at": "2026-01-01", "updated_at": "2026-01-01", "actor": "system"}
        connection.execute("INSERT INTO input_rules VALUES (?,?,?,?,?,?,?,?,?)", ("old-rule", "logical-1", 1, "fp-1", 1, payload["indicator_key"], "pending_confirmation", json.dumps(payload, ensure_ascii=False), "2026-01-01")); connection.execute("INSERT INTO rule_audit_log VALUES (1,'rule_discovered','2026-01-01','system',1,'fp-1','old-rule',1,NULL,NULL,'success',NULL)"); connection.commit(); connection.close()
        target = Path(self.temp.name) / "migrated.sqlite3"
        report = migrate_legacy_database(legacy, target)
        migrated = RuleStore(target)
        try:
            self.assertTrue(report["verified"])
            self.assertEqual((report["source_audits"], report["target_audits"]), (1, 1))
            self.assertEqual(migrated.get_rule("old-rule")["rule_id"], "old-rule")
            canonical = json.dumps(discovery(3), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            self.assertEqual(migrated.get_rule("old-rule")["snapshot_hash"], hashlib.sha256(canonical.encode()).hexdigest())
        finally: migrated.close()

    def test_backup_is_byte_identical_and_hash_verified(self):
        self.create(size=10)
        backup = Path(self.temp.name) / "rules.backup.sqlite3"
        report = backup_database(self.path, backup)
        self.assertEqual(backup.read_bytes(), self.path.read_bytes())
        self.assertEqual(report["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())


if __name__ == "__main__": unittest.main()
