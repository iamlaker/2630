import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from rule_store import RuleStore
from tests.test_rule_store_v2 import discovery, metadata


class RuleHttpContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RuleStore(Path(self.temp.name) / "rules.sqlite3")
        self.rule = self.store.create_discovered_rule(metadata(), discovery(200), actor="system")

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def test_detail_and_formula_pages_have_bounded_response_sizes(self):
        detail = self.store.get_rule(self.rule["rule_id"])
        detail_bytes = len(json.dumps(detail, ensure_ascii=False).encode())
        page = self.store.get_formula_chains(self.rule["rule_id"], "2026", offset=0, limit=20)
        page_bytes = len(json.dumps(page, ensure_ascii=False).encode())
        self.assertLess(detail_bytes, 50_000)
        self.assertLess(page_bytes, 50_000)
        self.assertEqual(page["total"], 200)


if __name__ == "__main__": unittest.main()
