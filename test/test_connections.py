import copy
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from core.connections.adapters import (ApiKeyAdapter, LdapAdapter, OAuthAdapter, SupersetAdapter,
                                       WecomAppAdapter, identity, result)
from core.connections.broker import ConnectionBroker
from core.connections.errors import ConnectionError
from core.connections.store import ConnectionStore
from core.connections.templates import TemplateCatalog, read_bundle, validate_template


class Protector:
    # Production DPAPI is tested separately; deterministic encryption allows process fixtures.
    def protect(self, value):
        return bytes(b ^ 0xA5 for b in value)

    def unprotect(self, value):
        return bytes(b ^ 0xA5 for b in value)


def template(adapter="api_key", **kwargs):
    item = {"id": "example", "version": 1, "name": "测试服务", "adapter": adapter,
            "service": "example", "base_url": "https://service.example", "parameters": {}}
    item.update(kwargs)
    return item


class FakeAdapter:
    refreshes = 0
    def __init__(self, template):
        self.template = template

    def login(self, inputs, **kwargs):
        return result(identity("issuer", inputs.get("subject", "user"), inputs.get("tenant", "company")),
                      {"token": inputs.get("token", "very-secret-token")}, scopes=inputs.get("scopes", ["read"]))

    def verify(self, item):
        return result(item["identity"], item["credentials"], scopes=item["scopes"])

    def refresh(self, item):
        time.sleep(0.04)
        type(self).refreshes += 1
        return result(item["identity"], {"token": "rotated-secret-token"}, scopes=item["scopes"])

    def headers(self, credentials):
        return {"Authorization": "Bearer " + credentials["token"]}


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConnectionStore(self.temp.name, Protector())
        self.broker = ConnectionBroker(self.temp.name, self.temp.name, store=self.store, adapters=FakeAdapter)
        FakeAdapter.refreshes = 0

    def connected(self, **inputs):
        item = self.store.create(validate_template(template()))
        return self.broker.authenticate(item["id"], inputs)

    def binding(self, item, task="task", capability="skill-a", operations=("read",), resources=()):
        self.broker.grant(item["id"], capability, operations, resources)
        return self.broker.binding(task, capability, connection_id=item["id"], operations=operations, resources=resources)

    def test_constructing_and_empty_catalog_do_not_create_database_or_network(self):
        with patch("requests.request", side_effect=AssertionError("network")):
            self.assertEqual(self.store.list(), [])
            self.assertEqual(self.broker.catalog.load(), ([], []))
            self.assertIsNone(self.store.selection("skill", "default"))
        self.assertFalse(os.path.exists(self.store.path))

    def test_secret_is_encrypted_and_absent_from_public_records(self):
        item = self.connected()
        self.assertNotIn("very-secret-token", json.dumps(self.store.list()))
        with open(self.store.path, "rb") as stream:
            self.assertNotIn(b"very-secret-token", stream.read())
        self.assertEqual(self.store.get(item["id"], secret=True)["credentials"]["token"], "very-secret-token")

    def test_windows_dpapi_roundtrip(self):
        if os.name != "nt":
            self.skipTest("Windows DPAPI")
        from core.variable_store import WindowsDpapiProtector
        crypt = WindowsDpapiProtector()
        protected = crypt.protect(b"connection-test-not-a-real-secret")
        self.assertEqual(crypt.unprotect(protected), b"connection-test-not-a-real-secret")
        self.assertNotIn(b"connection-test-not-a-real-secret", protected)

    def test_new_capability_does_not_inherit_grant(self):
        item = self.connected()
        self.binding(item)
        with self.assertRaisesRegex(ConnectionError, "尚未获得"):
            self.broker.binding("task", "new-plugin", connection_id=item["id"], operations=["read"])

    def test_multi_capability_reuse(self):
        item = self.connected()
        for cap in ("library:example", "skill", "mcp:example"):
            binding = self.binding(item, capability=cap)
            self.assertEqual(self.broker.execute(binding, "read", lambda _: {"ok": True}), {"ok": True})

    def test_selection_changes_do_not_change_existing_binding(self):
        a, b = self.connected(), self.connected(subject="other")
        old = self.binding(a)
        self.broker.grant(b["id"], "skill-a", ["read"])
        self.store.select("skill-a", "default", b["id"])
        self.assertEqual(self.broker.binding("task", "skill-a", operations=["read"])["id"], old["id"])
        self.assertEqual(self.broker.binding("new-task", "skill-a", operations=["read"])["connection_id"], b["id"])

    def test_scoped_grant_does_not_allow_unbounded_binding(self):
        item = self.connected()
        self.broker.grant(item["id"], "skill", ["read"], ["kb-1"])
        with self.assertRaises(ConnectionError):
            self.broker.binding("task", "skill", connection_id=item["id"], operations=["read"])
        bound = self.broker.binding("task", "skill", connection_id=item["id"], operations=["read"], resources=["kb-1"])
        with self.assertRaises(ConnectionError):
            self.broker.execute(bound, "read", lambda _: {}, resources=["kb-2"])

    def test_revoke_invalidates_live_binding(self):
        item = self.connected()
        bound = self.binding(item)
        self.broker.revoke(item["id"], "skill-a")
        call = Mock()
        with self.assertRaises(ConnectionError):
            self.broker.execute(bound, "read", call)
        call.assert_not_called()

    def test_reauthentication_with_different_identity_preserves_old_secret(self):
        item = self.connected()
        for changes in ({"subject": "different"}, {"tenant": "different"}, {"scopes": ["write"]}):
            with self.assertRaises(ConnectionError):
                self.broker.authenticate(item["id"], changes)
            self.assertEqual(self.store.get(item["id"], secret=True)["credentials"]["token"], "very-secret-token")

    def test_same_identity_reauth_preserves_binding(self):
        item = self.connected()
        bound = self.binding(item)
        self.broker.authenticate(item["id"], {"token": "new-secret-token"})
        self.store.check_binding(bound, "read")

    def test_cancellation_drops_late_auth_result(self):
        item = self.connected()
        with self.assertRaises(ConnectionError):
            self.broker.authenticate(item["id"], {"token": "new-token"}, cancelled=lambda: True)
        self.assertEqual(self.store.get(item["id"], secret=True)["credentials"]["token"], "very-secret-token")

    def test_disconnect_during_login_cannot_resurrect_connection(self):
        item = self.connected()
        base = FakeAdapter.login
        def login(adapter, inputs, **kwargs):
            self.store.disconnect(item["id"])
            return base(adapter, inputs, **kwargs)
        with patch.object(FakeAdapter, "login", login), self.assertRaises(ConnectionError):
            self.broker.authenticate(item["id"], {})
        self.assertEqual(self.store.get(item["id"])["state"], "disconnected")

    def test_concurrent_refresh_reuses_one_rotation(self):
        item = self.connected()
        binding = self.binding(item)
        revision = self.store.get(item["id"])["revision"]
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: self.broker.refresh(binding, revision), range(5)))
        self.assertEqual(FakeAdapter.refreshes, 1)
        self.assertTrue(all(r["credentials"]["token"] == "rotated-secret-token" for r in results))

    def test_auth_failure_retries_read_once_only(self):
        binding = self.binding(self.connected())
        call = Mock(side_effect=ConnectionError("authentication_required", status=401))
        with self.assertRaises(ConnectionError):
            self.broker.execute(binding, "read", call, safe_retry=True)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(FakeAdapter.refreshes, 1)

    def test_permission_failure_does_not_refresh(self):
        binding = self.binding(self.connected())
        with self.assertRaises(ConnectionError):
            self.broker.execute(binding, "read", Mock(side_effect=ConnectionError("forbidden", status=403)), safe_retry=True)
        self.assertEqual(FakeAdapter.refreshes, 0)

    def test_unknown_write_is_not_replayed(self):
        binding = self.binding(self.connected(), operations=["write"])
        call = Mock(side_effect=ConnectionError("authentication_required", status=401))
        with self.assertRaises(ConnectionError) as caught:
            self.broker.execute(binding, "write", call)
        self.assertEqual(caught.exception.code, "outcome_unknown")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(FakeAdapter.refreshes, 0)

    def test_secret_redacted_recursively_from_tool_result(self):
        binding = self.binding(self.connected())
        output = self.broker.execute(binding, "read", lambda _: {"content": ["echo very-secret-token"]})
        self.assertNotIn("very-secret-token", json.dumps(output))

    def test_target_escape_is_rejected(self):
        t = template(base_url="https://service.example/api")
        for url in ("https://other.example/api", "https://service.example/apix", "https://service.example/api/../secret",
                    "https://service.example/api/%2e%2e/secret", "http://service.example/api"):
            with self.assertRaises(ConnectionError):
                self.broker.check_target(t, url)
        self.broker.check_target(t, "https://service.example/api/items")

    def test_wait_is_persisted_without_operation_payload_and_cancelled(self):
        binding = self.binding(self.connected())
        self.broker.waiter = lambda *_: False
        with self.assertRaises(ConnectionError):
            self.broker.await_auth(binding, "read")
        reopened = ConnectionStore(self.temp.name, Protector())
        waits = reopened.waits()
        self.assertEqual(waits[0]["status"], "cancelled")
        self.assertNotIn("credentials", json.dumps(waits))
        self.assertNotIn("arguments", json.dumps(waits))

    def test_wait_resume_rechecks_identity_and_grant(self):
        item = self.connected()
        binding = self.binding(item)
        def waiter(*_):
            self.broker.authenticate(item["id"], {})
            self.broker.revoke(item["id"], "skill-a")
            return True
        self.broker.waiter = waiter
        with self.assertRaises(ConnectionError):
            self.broker.await_auth(binding, "read")

    def test_cancelled_wait_cannot_resume(self):
        item = self.connected()
        binding = self.binding(item)
        def waiter(*_):
            self.broker.authenticate(item["id"], {})
            self.store.cancel_task(binding["task_id"])
            return True
        self.broker.waiter = waiter
        with self.assertRaises(ConnectionError) as caught:
            self.broker.await_auth(binding, "read")
        self.assertEqual(caught.exception.code, "cancelled")

    def test_delete_does_not_restore_legacy_implicitly(self):
        item = self.connected()
        self.store.select("skill-a", "default", item["id"])
        self.store.delete(item["id"])
        self.assertEqual(self.store.selection("skill-a", "default"), item["id"])

    def test_template_changes_do_not_mutate_live_connection(self):
        self.broker.catalog.write(self.broker.catalog.distributed_path, [template()])
        item = self.broker.create("example")
        self.broker.catalog.write(self.broker.catalog.distributed_path, [template(version=2, base_url="https://new.example")])
        self.assertEqual(self.store.get(item["id"])["template"]["base_url"], "https://service.example")


class TemplateTests(unittest.TestCase):
    def test_equal_version_different_sources_require_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog = TemplateCatalog(folder, folder)
            catalog.write(catalog.distributed_path, [template()])
            catalog.write(catalog.local_path, [template(base_url="https://another.example")])
            items, errors = catalog.load()
            self.assertEqual(items, [])
            self.assertEqual(errors[0]["code"], "template_conflict")

    def test_malformed_requirement_types_are_structured_errors(self):
        from core.connections.broker import requirement_list
        base = {"id": "resource", "service": "service", "operations": ["read"]}
        for change in ({"id": []}, {"auth": []}, {"delivery": {}}, {"resources": "scope"}):
            with self.subTest(change=change), self.assertRaises(ConnectionError):
                requirement_list([{**base, **change}])

    def test_malformed_duplicate_ids_cannot_break_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "templates.json")
            with open(path, "w") as stream:
                json.dump({"version": 1, "templates": [template(), {"id": []}, {"id": []}]}, stream)
            valid, errors = read_bundle(path)
            self.assertEqual(len(valid), 1)
            self.assertEqual(len(errors), 2)

    def test_distributable_examples_validate_without_network(self):
        from pathlib import Path
        path = Path(__file__).resolve().parents[1] / "docs/examples/connection_templates.json"
        items, errors = read_bundle(str(path))
        self.assertFalse(errors)
        self.assertEqual(len(items), 8)

    def test_forbidden_credentials_and_unknown_fields(self):
        for patch_value in ({"password": "secret"}, {"parameters": {"token": "secret"}}, {"script": "run.py"}, {"adapter": "unknown"},
                            {"base_url": "https://user:password@service.example"}, {"parameters": {"header": "bad\nheader"}}):
            with self.assertRaises(ConnectionError):
                validate_template(template(**patch_value))

    def test_bad_template_does_not_hide_valid_template(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "templates.json")
            with open(path, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "templates": [template(), {"id": "broken"}]}, stream)
            valid, errors = read_bundle(path)
            self.assertEqual(len(valid), 1)
            self.assertEqual(len(errors), 1)

    def test_duplicate_ids_do_not_use_first_or_last_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "templates.json")
            with open(path, "w") as stream:
                json.dump({"version": 1, "templates": [template(), template(base_url="https://other.example")]}, stream)
            self.assertEqual(read_bundle(path)[0], [])

    def test_import_conflicts_and_locked_templates(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog = TemplateCatalog(folder, folder)
            path = os.path.join(folder, "incoming.json")
            catalog.write(catalog.distributed_path, [template()])
            catalog.write(path, [template(version=2)])
            with self.assertRaises(ConnectionError):
                catalog.import_file(path)
            catalog.import_file(path, replace=True)
            self.assertEqual(catalog.get("example")["version"], 2)
            catalog.write(catalog.distributed_path, [template(version=3, locked=True)])
            with self.assertRaises(ConnectionError):
                catalog.import_file(path, replace=True)


class AdapterTests(unittest.TestCase):
    def response(self, payload, status=200):
        return Mock(status_code=status, json=Mock(return_value=payload))

    def test_api_key_without_verify_is_saved_not_ready(self):
        output = ApiKeyAdapter(template()).login({"token": "token-for-test"})
        self.assertFalse(output["verified"])
        self.assertEqual(output["identity"]["kind"], "credential")

    def test_remote_error_cannot_echo_secret(self):
        http = Mock()
        http.request.return_value = self.response({"error": "secret-in-remote-error"}, 401)
        adapter = SupersetAdapter(template("superset"), http)
        with self.assertRaises(ConnectionError) as caught:
            adapter.login({"password": "secret-in-remote-error"})
        self.assertNotIn("secret", str(caught.exception))

    def test_superset_ldap_uses_business_login_and_validates_identity(self):
        http = Mock()
        http.request.side_effect = [self.response({"access_token": "access", "refresh_token": "refresh"}),
                                    self.response({"result": {"id": 42, "username": "tester"}})]
        output = SupersetAdapter(template("superset", parameters={"provider": "ldap"}), http).login({"username": "tester", "password": "password"})
        self.assertEqual(output["identity"]["subject"], "42")
        self.assertEqual(http.request.call_args_list[0].kwargs["json"]["provider"], "ldap")
        self.assertNotIn("password", output["credentials"])

    def test_ldap_requires_tls_and_service_separation(self):
        with self.assertRaises(ConnectionError):
            validate_template(template("ldap", parameters={"host": "directory", "tls": "none", "base_dn": "dc=example", "user_filter": "(uid={username})"}))
        with self.assertRaises(ConnectionError):
            LdapAdapter(template("ldap")).headers({})

    def test_wecom_application_is_not_a_member(self):
        http = Mock()
        http.request.side_effect = [self.response({"access_token": "app-token", "expires_in": 7200}),
                                   self.response({"agentid": 100, "name": "应用"})]
        output = WecomAppAdapter(template("wecom_app", base_url="https://qyapi.weixin.qq.com", parameters={"corp_id": "corp", "agent_id": "100"}), http).login({"secret": "app-secret"})
        self.assertEqual(output["identity"]["kind"], "application")
        self.assertEqual(output["identity"]["tenant"], "corp")

    def test_oauth_wrong_discovery_issuer_is_rejected(self):
        http = Mock()
        http.request.return_value = self.response({"issuer": "https://other.example"})
        adapter = OAuthAdapter(template("oauth2", parameters={"issuer": "https://id.example", "client_id": "desktop"}), http)
        with self.assertRaises(ConnectionError):
            adapter.metadata()

    def test_ldap_tls_certificate_validation_and_filter_escaping(self):
        import ldap3
        import ssl
        t = template("ldap", parameters={"host": "directory", "tls": "starttls", "base_dn": "dc=example", "user_filter": "(uid={username})"})
        search, bind = Mock(), Mock()
        entry = Mock(entry_dn="uid=person,dc=example")
        from unittest.mock import MagicMock
        entry = MagicMock(entry_dn="uid=person,dc=example")
        entry.__getitem__.return_value.value = "user-uuid"
        search.entries = [entry]
        contexts = []
        for value in (search, bind):
            ctx = MagicMock()
            ctx.__enter__.return_value = value
            contexts.append(ctx)
        with patch.object(ldap3, "Tls", wraps=ldap3.Tls) as tls, patch.object(ldap3, "Server"), patch.object(ldap3, "Connection", side_effect=contexts) as connect:
            output = LdapAdapter(t).login({"username": "person*)(uid=*)", "password": "test-password"})
        self.assertEqual(tls.call_args.kwargs["validate"], ssl.CERT_REQUIRED)
        self.assertEqual(connect.call_args_list[0].kwargs["auto_bind"], ldap3.AUTO_BIND_TLS_BEFORE_BIND)
        self.assertIn("\\2a", search.search.call_args.args[1])
        self.assertEqual(output["identity"]["subject"], "user-uuid")
        self.assertEqual(output["credentials"], {})

    def test_optional_dependency_failure_is_local_to_adapter(self):
        import sys
        with patch.dict(sys.modules, {"authlib.integrations.requests_client": None}):
            with self.assertRaises(ConnectionError) as caught:
                OAuthAdapter(template("oauth2")).client({"client_id": "test"})
        self.assertEqual(caught.exception.code, "dependency_missing")
        self.assertFalse(ApiKeyAdapter(template()).login({"token": "saved-token"})["verified"])

    def test_device_authorization_wait_and_success(self):
        http = Mock()
        http.request.side_effect = [self.response({"device_code": "private-device", "user_code": "VISIBLE-CODE", "verification_uri": "https://login.example/device", "expires_in": 60, "interval": 1}),
            self.response({"access_token": "device-token", "scope": "read", "token_type": "Bearer"}), self.response({"sub": "person"})]
        adapter = OAuthAdapter(template("oauth2"), http)
        notices = []
        output = adapter.device_login({"client_id": "app", "device_authorization_endpoint": "https://login.example/device-code",
            "token_endpoint": "https://login.example/token", "userinfo_endpoint": "https://login.example/me", "scope": "read", "oidc": False}, None, notices.append)
        self.assertEqual(output["identity"]["subject"], "person")
        self.assertEqual(notices[0]["user_code"], "VISIBLE-CODE")
        self.assertNotIn("private-device", str(notices))


if __name__ == "__main__":
    unittest.main()
