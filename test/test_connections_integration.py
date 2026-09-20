import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import Mock, patch

from core.connections.adapters import OAuthAdapter, identity, result
from core.connections.broker import ConnectionBroker
from core.connections.errors import ConnectionError
from core.connections.knowledge import KnowledgeConnectionRouter, adopt_references
from core.connections.store import ConnectionStore
from core.connections.templates import validate_template
from core.execution_authorization import ExecutionAuthorization, bind_authorization
from core.knowledge_library import KnowledgeService, KnowledgeStore
from core.knowledge_sources import context_message
from test_connections import FakeAdapter, Protector, template
from test_knowledge_library import TestProtector, WeKnoraFixture


class OAuthLocalServerTests(unittest.TestCase):
    def setUp(self):
        self.verifier_ok = False
        self.authorization = {}
        self.refresh_count = 0
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def output(self, payload):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def do_GET(self):
                if self.path == "/me":
                    self.output({"sub": "person-123", "tid": "tenant-1"})
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self):
                values = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
                if values.get("grant_type") == ["authorization_code"]:
                    verifier = values["code_verifier"][0]
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                    owner.verifier_ok = (challenge == owner.authorization["code_challenge"][0] and owner.authorization["code_challenge_method"] == ["S256"])
                elif values.get("grant_type") == ["refresh_token"]:
                    owner.refresh_count += 1
                self.output({"access_token": "local-test-access", "refresh_token": "local-test-refresh",
                             "token_type": "Bearer", "expires_in": 3600, "scope": "read"})

            def log_message(self, *_args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.adapter = OAuthAdapter(template("oauth2", base_url=self.base, parameters={
            "client_id": "cowork-test", "authorization_endpoint": self.base + "/authorize",
            "token_endpoint": self.base + "/token", "userinfo_endpoint": self.base + "/me", "scope": "read", "oidc": False}))

    def browser(self, payload):
        self.authorization = parse_qs(urlsplit(payload["url"]).query)
        target = self.authorization["redirect_uri"][0]
        def callback():
            import requests
            requests.get(target, params={"state": self.authorization["state"][0], "code": "one-time-code"}, timeout=5)
        thread = threading.Thread(target=callback, daemon=True)
        thread.start()

    def test_real_loopback_pkce_token_and_refresh(self):
        output = self.adapter.login({}, progress=self.browser)
        self.assertTrue(self.verifier_ok)
        self.assertEqual(output["identity"]["subject"], "person-123")
        self.assertEqual(output["scopes"], ["read"])
        refreshed = self.adapter.refresh(output)
        self.assertEqual(refreshed["identity"], output["identity"])
        self.assertEqual(self.refresh_count, 1)

    def test_wrong_callback_state_is_rejected_and_cancellable(self):
        cancelled = threading.Event()
        def browser(payload):
            query = parse_qs(urlsplit(payload["url"]).query)
            def callback():
                import requests
                response = requests.get(query["redirect_uri"][0], params={"state": "wrong", "code": "do-not-exchange"}, timeout=5)
                self.assertEqual(response.status_code, 400)
                cancelled.set()
            threading.Thread(target=callback, daemon=True).start()
        with self.assertRaises(ConnectionError) as caught:
            self.adapter.login({}, progress=browser, cancelled=cancelled.is_set)
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertFalse(self.verifier_ok)

    def test_client_credentials_identity_is_stable(self):
        adapter = OAuthAdapter(template("oauth2", base_url=self.base, parameters={"client_id": "app", "token_endpoint": self.base + "/token", "flow": "client_credentials", "scope": "read"}))
        output = adapter.login({"client_secret": "local-secret"})
        self.assertEqual(output["identity"]["kind"], "application")
        self.assertEqual(output["credentials"]["client_secret"], "local-secret")
        self.assertEqual(adapter.refresh(output)["identity"], output["identity"])

    def test_manual_endpoints_do_not_require_discovery(self):
        adapter = OAuthAdapter(template("oauth2", parameters={"client_id": "app", "issuer": "https://issuer.example",
            "token_endpoint": self.base + "/token", "flow": "client_credentials", "scope": "read"}))
        with patch.object(adapter, "request", side_effect=AssertionError("unexpected discovery")):
            self.assertEqual(adapter.metadata()["token_endpoint"], self.base + "/token")


class OidcTests(unittest.TestCase):
    def test_signature_issuer_audience_nonce_and_expiry(self):
        from authlib.jose import JsonWebKey, JsonWebToken
        key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
        public = key.as_dict(is_private=False)
        payload = {"sub": "person", "iss": "https://issuer.example", "aud": "cowork", "nonce": "correct", "exp": int(time.time()) + 300, "iat": int(time.time())}
        http = Mock()
        http.request.return_value = Mock(status_code=200, json=Mock(return_value={"keys": [public]}))
        adapter = OAuthAdapter(template("oauth2"), http)
        params = {"issuer": "https://issuer.example", "client_id": "cowork", "jwks_uri": "https://issuer.example/keys", "scope": "openid", "oidc": True}
        def token(claims):
            return {"access_token": "access", "id_token": JsonWebToken(["RS256"]).encode({"alg": "RS256"}, claims, key).decode()}
        self.assertEqual(adapter._who(params, token(payload), "correct")["subject"], "person")
        for changes in ({"iss": "https://wrong.example"}, {"aud": "wrong"}, {"nonce": "wrong"}, {"exp": int(time.time()) - 1000}):
            with self.subTest(changes=changes), self.assertRaises(ConnectionError):
                adapter._who(params, token({**payload, **changes}), "correct")


class HostIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConnectionStore(self.temp.name, Protector())
        self.broker = ConnectionBroker(self.temp.name, self.temp.name, store=self.store, adapters=FakeAdapter)

    def connected(self, t=None):
        item = self.store.create(validate_template(t or template()))
        return self.broker.authenticate(item["id"], {})

    def test_mcp_injects_headers_only_in_transport_and_redacts_output(self):
        from core.mcp_client import call_mcp_tool
        item = self.connected()
        self.store.activate(item["id"], [("mcp:example", "default", ["call", "discover"], [])])
        auth = ExecutionAuthorization("session", "run", True, "")
        server = {"id": "example", "url": "https://service.example/mcp", "transport": "streamable-http", "enabled": True}
        captured = {}
        async def call(config, tool, arguments):
            captured.update(config)
            self.assertEqual(arguments, {"query": "hello"})
            return {"text": "echo very-secret-token", "is_error": False}
        with patch("core.connections.mcp.broker_for", return_value=self.broker), patch("core.mcp_client._call_mcp_tool_async", call), bind_authorization(auth):
            output = call_mcp_tool(server, "search", {"query": "hello"})
        self.assertEqual(captured["headers"]["Authorization"], "Bearer very-secret-token")
        self.assertNotIn("headers", server)
        self.assertNotIn("very-secret-token", json.dumps(output))

    def test_mcp_default_change_and_legacy_switch_do_not_change_active_task(self):
        from core.connections.mcp import selected_connection
        item = self.connected()
        self.store.activate(item["id"], [("mcp:example", "default", ["call"], [])])
        auth = ExecutionAuthorization("session", "run", True, "")
        self.broker.binding("session:run", "mcp:example", operations=["call"])
        self.store.select("mcp:example", "default", None)
        with patch("core.connections.mcp.broker_for", return_value=self.broker), bind_authorization(auth):
            self.assertEqual(selected_connection({"id": "example"}), item["id"])

    def test_legacy_task_does_not_switch_to_new_default(self):
        from core.connections.mcp import selected_connection
        from core.execution_authorization import authorization_from_host_snapshot
        auth = ExecutionAuthorization("session", "run", True, "")
        with patch("core.connections.mcp.broker_for", return_value=self.broker), bind_authorization(auth):
            self.assertIsNone(selected_connection({"id": "example"}))
            item = self.connected()
            self.store.select("mcp:example", "default", item["id"])
            self.assertIsNone(selected_connection({"id": "example"}))
        restored = authorization_from_host_snapshot(auth.runtime_snapshot(), session_id="session", run_id="run")
        with patch("core.connections.mcp.broker_for", return_value=self.broker), bind_authorization(restored):
            self.assertIsNone(selected_connection({"id": "example"}))

    def test_skill_connection_metadata_does_not_change_prompt_or_tool_schema(self):
        from core.skill_manager import SkillManager
        manager = SkillManager.__new__(SkillManager)
        meta = {"name": "sample", "description": "Read a service"}
        original = {"name": "sample", "workflow": ["Read status"]}
        unified = {**original, "connection_requirements": [{"id": "default", "service": "company-api", "auth": "required", "operations": ["read"]}]}
        self.assertEqual(manager._build_skill_prompt("sample", meta, "body", original, ["status"]),
                         manager._build_skill_prompt("sample", meta, "body", unified, ["status"]))

    def test_cancel_during_grant_prompt_does_not_save_grant(self):
        item = self.connected()
        auth = ExecutionAuthorization("session", "run", True, "")
        def reply(*_args, **_kwargs):
            auth.cancel()
            return {"status": "completed", "approved": True}
        with bind_authorization(auth), patch("core.interaction.interaction_service.create_request", side_effect=reply):
            with self.assertRaises(Exception):
                self.broker.binding("session:run", "new-capability", connection_id=item["id"], operations=["read"])
        self.assertEqual(self.store.grants(item["id"]), [])

    def test_sample_capability_uses_host_api_without_credential_parameters(self):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "examples/connection-plugin/impl.py"
        spec = importlib.util.spec_from_file_location("connection_example", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        access = Mock()
        access.request.return_value = {"status": "ready"}
        self.assertEqual(module.connected_service_status({"connections": access}), {"status": "ready"})
        self.assertEqual(module.TOOL_EXPORTS[0]["parameters"]["properties"], {})
        access.request.assert_called_once_with("default", "read", "GET", "https://api.example.com/status")

    def test_legacy_mcp_path_does_not_read_vault_or_change_config(self):
        from core.mcp_client import call_mcp_tool
        async def call(config, *_args):
            self.assertEqual(config["headers"], {"X-Existing": "value"})
            return {"is_error": False, "text": "ok"}
        server = {"id": "old", "transport": "streamable-http", "headers": {"X-Existing": "value"}}
        with patch("core.connections.mcp.broker_for", return_value=self.broker), patch.object(self.store, "crypt", side_effect=AssertionError("vault read")), patch("core.mcp_client._call_mcp_tool_async", call):
            self.assertEqual(call_mcp_tool(server, "existing")["status"], "ok")
        self.assertFalse(self.store.exists)

    def test_knowledge_route_preserves_old_store_and_reference_scope(self):
        legacy_store = KnowledgeStore(self.temp.name, TestProtector())
        fixture = WeKnoraFixture()
        legacy = KnowledgeService(legacy_store, fixture)
        old = legacy.login("http://localhost", "reader@example.test", "password")
        before = legacy_store.connection(secret=True)
        item = self.store.create(validate_template(template("weknora", service="weknora", base_url="http://localhost")))
        self.store.update(item["id"], {"state": "ready", "identity": identity("http://localhost", old["user_id"], old["tenant_id"]),
            "profile": {"label": old["email"]}}, credentials=before["credentials"])
        self.store.activate(item["id"], [("library:weknora", "weknora", ["read", "write"], []), ("knowledge-library", "weknora", ["read"], [])])
        adopt_references(self.broker, legacy_store, "weknora", item["id"])
        router = KnowledgeConnectionRouter(legacy, "weknora")
        router.broker = self.broker
        scope = router.snapshot()
        self.assertEqual(scope["connection_id"], old["connection_id"])
        self.assertIn("mine", router.catalog(scope))
        self.assertEqual(legacy_store.connection(secret=True), before)
        frozen = legacy.snapshot()
        self.assertIs(router._provider(frozen), legacy)
        self.store.select("library:weknora", "weknora", None)
        self.assertIsNot(router._provider(scope), legacy)

    def test_private_knowledge_binding_does_not_change_model_context(self):
        original = {"sources": {"weknora": {"refs": [{"source": "weknora", "title": "文档", "collection_id": "kb", "item_id": "doc"}]}}, "default_source": "weknora"}
        unified = copy.deepcopy(original)
        unified["sources"]["weknora"].update(_connection_ref="private-ref", _connection_task_id="private-task", identity={"subject": "private"})
        self.assertEqual(context_message(original, "request"), context_message(unified, "request"))

    def test_workspace_switch_creates_new_connection_without_rebinding_old_task(self):
        from core.connections.knowledge import unified_provider
        item = self.connected(template("weknora", service="weknora"))
        self.store.update(item["id"], {}, credentials={"token": "old-access", "refresh_token": "old-refresh"})
        self.store.activate(item["id"], [("library:weknora", "weknora", ["read"], [])])
        binding = self.broker.binding("active-task", "library:weknora", "weknora", operations=["read"])
        adapter = Mock()
        adapter.headers.return_value = {"Authorization": "Bearer old-access"}
        adapter.request.side_effect = [{"memberships": [{"tenant_id": 2}]},
            {"active_tenant": {"id": 2}, "user": {"id": "user"}, "token": "new-access", "refresh_token": "new-refresh"}]
        self.broker.adapters = lambda _: adapter
        legacy = KnowledgeService(KnowledgeStore(self.temp.name, TestProtector()), WeKnoraFixture())
        provider = unified_provider(legacy, self.broker, item["id"], "weknora")
        new = provider.switch_tenant("2")
        self.assertNotEqual(new["id"], item["id"])
        self.assertEqual(self.store.selection("library:weknora", "weknora"), new["id"])
        self.assertEqual(self.store.check_binding(binding)["identity"]["tenant"], "company")
        self.assertEqual(self.store.get(item["id"], secret=True)["credentials"]["token"], "old-access")

    def test_reopened_store_keeps_wait_without_executing_operation(self):
        item = self.connected()
        self.broker.grant(item["id"], "skill", ["read"])
        binding = self.broker.binding("historical-task", "skill", connection_id=item["id"], operations=["read"])
        self.store.wait(binding, "read")
        with patch("requests.request", side_effect=AssertionError("history must not execute")):
            reopened = ConnectionBroker(self.temp.name, self.temp.name, store=ConnectionStore(self.temp.name, Protector()))
            self.assertEqual(reopened.store.waits()[0]["status"], "waiting")
            self.assertEqual(reopened.store.binding("historical-task", "skill", "default"), binding)

    def test_cross_process_refresh_rotates_once(self):
        item = self.connected()
        self.broker.grant(item["id"], "skill", ["read"])
        binding = self.broker.binding("task", "skill", connection_id=item["id"], operations=["read"])
        revision = self.store.get(item["id"])["revision"]
        payload_path = Path(self.temp.name) / "binding.json"
        payload_path.write_text(json.dumps(binding), encoding="utf-8")
        code = r'''
import sys, json, time
from pathlib import Path
from core.connections.broker import ConnectionBroker
from core.connections.store import ConnectionStore
from core.connections.adapters import result
class Protector:
    def protect(self,b): return bytes(x ^ 0xA5 for x in b)
    def unprotect(self,b): return bytes(x ^ 0xA5 for x in b)
class Adapter:
    def __init__(self,t): pass
    def refresh(self,c):
        with open(Path(sys.argv[1])/'rotations.txt','a') as f: f.write('rotation\n')
        time.sleep(.2)
        return result(c['identity'], {'token':'new-process-token'}, scopes=c['scopes'])
folder=sys.argv[1]
broker=ConnectionBroker(folder,folder,store=ConnectionStore(folder,Protector()),adapters=Adapter)
binding=json.loads((Path(folder)/'binding.json').read_text(encoding='utf-8'))
broker.refresh(binding,int(sys.argv[2]))
'''
        from core.process_utils import subprocess_kwargs_no_window
        processes = [subprocess.Popen([sys.executable, "-c", code, self.temp.name, str(revision)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, **subprocess_kwargs_no_window()) for _ in range(3)]
        outcomes = []
        try:
            for process in processes:
                _, error = process.communicate(timeout=20)
                outcomes.append((process.returncode, error.decode(errors="replace")))
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate()
        for status, error in outcomes:
            self.assertEqual(status, 0, error)
        self.assertEqual((Path(self.temp.name) / "rotations.txt").read_text().count("rotation"), 1)


if __name__ == "__main__":
    unittest.main()
