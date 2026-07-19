import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from workbench import build_handler


class StubService:
    def __init__(self):
        self.calls = []

    def rescan_template(self, template_version_id, *, actor, force=False):
        self.calls.append(("rescan", template_version_id, actor, force))
        return {"created_versions": 0}

    def update_rule(self, template_version_id, rule_id, payload):
        self.calls.append(("update", rule_id, payload.get("action")))
        return {"rule_id": rule_id, "rule_version": 2}

    def activate_rule_set(self, template_version_id, *, actor):
        self.calls.append(("activate", template_version_id, actor))
        return {"active": True}

    def deactivate_rule_set(self, template_version_id, *, actor):
        self.calls.append(("deactivate", template_version_id, actor))
        return {"active": False}


class AdminSessionHttpTests(unittest.TestCase):
    TOKEN = "test-admin-token"

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.service = StubService()
        handler = build_handler(cls.service, Path(cls.temp.name), cls.TOKEN)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.temp.cleanup()

    def setUp(self):
        self.service.calls.clear()

    def request(self, method, path, body=None, cookie=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        payload = json.loads(response.read() or b"{}")
        set_cookie = response.getheader("Set-Cookie")
        connection.close()
        return response.status, payload, set_cookie

    def login_cookie(self):
        status, _, set_cookie = self.request("POST", "/api/admin/login", {"token": self.TOKEN})
        self.assertEqual(status, 200)
        self.assertIsNotNone(set_cookie)
        return set_cookie.split(";")[0]

    def test_admin_session_can_maintain_rules(self):
        cookie = self.login_cookie()
        status, payload, _ = self.request("POST", "/api/rules/rule-1", {"template_version_id": 1, "action": "confirm"}, cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload["rule_version"], 2)
        status, _, _ = self.request("POST", "/api/rules/rescan", {"template_version_id": 1, "force": True}, cookie)
        self.assertEqual(status, 200)
        status, payload, _ = self.request("POST", "/api/rule-set/activate", {"template_version_id": 1}, cookie)
        self.assertEqual(status, 200)
        self.assertTrue(payload["active"])
        status, payload, _ = self.request("POST", "/api/rule-set/deactivate", {"template_version_id": 1}, cookie)
        self.assertEqual(status, 200)
        self.assertFalse(payload["active"])
        self.assertEqual([call[0] for call in self.service.calls], ["update", "rescan", "activate", "deactivate"])

    def test_login_rejects_wrong_token(self):
        status, payload, set_cookie = self.request("POST", "/api/admin/login", {"token": "wrong"})
        self.assertEqual(status, 403)
        self.assertIn("invalid", payload["error"])
        self.assertIsNone(set_cookie)

    def test_login_sets_session_cookie_and_session_reports_admin(self):
        cookie = self.login_cookie()
        self.assertTrue(cookie.startswith("rule_admin_session="))
        status, payload, _ = self.request("GET", "/api/admin/session", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"admin": True})

    def test_rule_mutations_require_admin_session(self):
        for path, body in [
            ("/api/rules/rescan", {"template_version_id": 1, "force": True}),
            ("/api/rules/rule-1", {"template_version_id": 1, "action": "confirm"}),
            ("/api/rule-set/activate", {"template_version_id": 1}),
            ("/api/rule-set/deactivate", {"template_version_id": 1}),
        ]:
            status, payload, _ = self.request("POST", path, body)
            self.assertEqual(status, 403, path)
            self.assertIn("admin", payload["error"])
        self.assertEqual(self.service.calls, [])

    def test_session_endpoint_reports_anonymous_as_non_admin(self):
        status, payload, _ = self.request("GET", "/api/admin/session")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"admin": False})


if __name__ == "__main__":
    unittest.main()
