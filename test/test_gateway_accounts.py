"""Gateway wire contracts and credential isolation without external accounts."""
import base64
import copy
import json
import unittest
from unittest.mock import patch

import httpx

from core.llm.gateway_accounts import GatewayAccount, GatewayError, encrypt_password, normalize_site_url, channels_from_selection
from core.llm.gateway_catalog import catalog_record, new_catalog_model, reconcile_catalog, normalize_gateway_source


class GatewayAccountTests(unittest.TestCase):
    def account(self, platform, handler):
        account = GatewayAccount("https://site.test/proxy/v1", platform, transport=httpx.MockTransport(handler))
        self.addCleanup(account.close)
        return account

    def response(self, request, data, platform="new_api", headers=None):
        payload = {"code": 0, "data": data} if platform == "sub2api" else {"success": True, "data": data}
        return httpx.Response(200, json=payload, headers=headers, request=request)

    def test_url_normalization_preserves_proxy_and_rejects_embedded_secrets(self):
        for raw in ("https://SITE.test/proxy/v1/", "https://site.test:443/proxy/api/v1", "https://site.test/proxy/api"):
            self.assertEqual(normalize_site_url(raw), "https://site.test/proxy")
        for raw in ("https://user:password@site.test", "https://site.test/?key=x", "file:///a", "https://site.test:bad"):
            with self.assertRaises(GatewayError):
                normalize_site_url(raw)

    def test_sub2api_login_totp_pagination_and_key_scoped_models(self):
        calls = []
        def handler(request):
            path = request.url.path.removeprefix("/proxy")
            calls.append((request.method, path, dict(request.headers)))
            if path == "/api/v1/settings/public":
                data = {"registration_enabled": True}
            elif path == "/api/v1/auth/login":
                self.assertEqual(json.loads(request.content), {"email": "a@b.test", "password": "secret"})
                data = {"requires_2fa": True, "temp_token": "temp"}
            elif path == "/api/v1/auth/login/2fa":
                self.assertEqual(json.loads(request.content), {"temp_token": "temp", "totp_code": "123456"})
                data = {"access_token": "management", "user": {"id": 2}}
            elif path == "/api/v1/keys":
                self.assertEqual(request.headers["authorization"], "Bearer management")
                page = int(request.url.params["page"])
                data = {"total": 2, "items": [{"id": page, "key": "sk-must-not-reach-ui", "name": f"Key {page}",
                                               "status": "active", "group": {"id": page, "name": "Group", "platform": "openai"}}]}
            elif path == "/api/v1/keys/1":
                data = {"key": "sk-relay"}
            elif path == "/v1/models":
                self.assertEqual(request.headers["authorization"], "Bearer sk-relay")
                self.assertNotIn("new-api-user", request.headers)
                data = [{"id": "arbitrary", "supported_endpoint_types": ["openai-response"]}]
            else:
                self.fail(path)
            return self.response(request, data, "sub2api")
        account = self.account("sub2api", handler)
        self.assertTrue(account.login("a@b.test", "secret")["requires_2fa"])
        account.complete_2fa("123456")
        keys = account.list_keys()
        self.assertEqual(len(keys), 2)
        self.assertNotIn("key", keys[0])
        result = account.discover(keys[0])
        self.assertEqual(result["records"][0]["api_protocol"], "responses")
        channels = channels_from_selection({"1": result}, {("1", "arbitrary")}, "My service")
        self.assertEqual(channels[0]["base_url"], "https://site.test/proxy/v1")
        self.assertNotIn("management", json.dumps(channels))
        self.assertFalse(any(path in {"/v1/responses", "/v1/chat/completions"} for _, path, _ in calls))

    def test_new_api_cookie_login_and_unprefixed_key(self):
        def handler(request):
            path = request.url.path.removeprefix("/proxy")
            if path == "/api/status":
                return self.response(request, {"version": "0.10"})
            if path == "/api/user/login":
                return self.response(request, {"id": 8}, headers={"set-cookie": "session=abc; Path=/"})
            self.assertEqual(request.headers["new-api-user"], "8")
            self.assertIn("session=abc", request.headers["cookie"])
            return self.response(request, {"key": "relay"})
        account = self.account("new_api", handler)
        account.login("user", "password")
        self.assertEqual(account.read_key("3"), "sk-relay")

    def test_new_api_new_challenge_and_masked_key_reveal(self):
        calls = []
        def handler(request):
            path = request.url.path.removeprefix("/proxy")
            calls.append((request.method, path))
            if path == "/api/status":
                data = {"version": "new"}
            elif path == "/api/user/login":
                data = {"require_verification": True, "flow_token": "flow", "methods": [{"method": "2fa", "available": True}]}
            elif path == "/api/user/login/verify":
                self.assertEqual(json.loads(request.content), {"flow_token": "flow", "method": "2fa", "code": "123456"})
                data = {"access_token": "jwt", "user": {"id": 6}}
            else:
                self.assertEqual(request.headers["authorization"], "Bearer jwt")
                self.assertNotIn("new-api-user", request.headers)
                data = {"key": "ab****cd" if request.method == "GET" else "sk-full"}
            return self.response(request, data)
        account = self.account("new_api", handler)
        account.login("u", "p")
        account.complete_2fa("123456")
        self.assertEqual(account.read_key("5"), "sk-full")
        self.assertIn(("POST", "/api/token/5/key"), calls)

    def test_rsa_and_long_unicode_password_wire_formats(self):
        from Crypto.PublicKey import RSA
        from Crypto.Cipher import PKCS1_OAEP, AES
        from Crypto.Hash import SHA256
        key = RSA.generate(2048)
        for password in ("password", "长密码" * 200):
            encoded = encrypt_password(password, key.publickey().export_key(), "kid")["password_encrypted"]
            if encoded.startswith("v2."):
                _, wrapped, nonce, ciphertext = encoded.split(".")
                secret = PKCS1_OAEP.new(key, hashAlgo=SHA256, label=b"password-v2").decrypt(base64.b64decode(wrapped))
                cipher = AES.new(secret, AES.MODE_GCM, nonce=base64.b64decode(nonce))
                cipher.update(b"password-v2:kid")
                data = base64.b64decode(ciphertext)
                decoded = cipher.decrypt_and_verify(data[:-16], data[-16:])
            else:
                decoded = PKCS1_OAEP.new(key, hashAlgo=SHA256).decrypt(base64.b64decode(encoded))
            self.assertEqual(decoded.decode(), password)

    def test_encrypted_login_uses_server_key_and_never_plain_password(self):
        from Crypto.PublicKey import RSA
        key = RSA.generate(2048)
        def handler(request):
            if request.url.path.endswith("/status"):
                data = {"version": "new", "password_login_encryption_enabled": True}
            elif request.url.path.endswith("encryption-key"):
                data = {"kid": "test", "public_key": key.publickey().export_key().decode()}
            else:
                body = json.loads(request.content)
                self.assertNotIn("password", body)
                self.assertEqual(body["encryption_key_id"], "test")
                data = {"access_token": "jwt", "user": {"id": 1}}
            return self.response(request, data)
        self.account("new_api", handler).login("user", "secret")

    def test_legacy_totp_cookie_is_kept_until_completed(self):
        def handler(request):
            if request.url.path.endswith("/status"):
                return self.response(request, {"version": "old"})
            if request.url.path.endswith("/login"):
                return self.response(request, {"require_2fa": True}, headers={"set-cookie": "session=pending; Path=/"})
            self.assertIn("session=pending", request.headers["cookie"])
            self.assertEqual(json.loads(request.content), {"code": "123456"})
            return self.response(request, {"id": 3})
        account = self.account("new_api", handler)
        account.login("u", "p")
        account.complete_2fa("123456")
        self.assertEqual(account.account_id, "3")

    def test_create_lost_response_reconciles_without_duplicate_post(self):
        state = {"name": "", "posts": 0}
        def handler(request):
            if request.method == "POST":
                state["posts"] += 1
                state["name"] = json.loads(request.content)["name"]
                raise httpx.ReadTimeout("secret server content", request=request)
            return self.response(request, {"items": [{"id": 11, "name": state["name"], "status": 1}], "total": 1})
        account = self.account("new_api", handler)
        with self.assertRaisesRegex(GatewayError, "超时"):
            account.create_key("Cowork")
        with self.assertRaisesRegex(GatewayError, "核对"):
            account.create_key("Cowork")
        self.assertEqual(account.reconcile_creation()["id"], "11")
        self.assertEqual(state["posts"], 1)
        self.assertIsNone(account.pending_creation)

    def test_expired_disabled_exhausted_and_empty_keys(self):
        data = {"items": [{"id": 1, "status": 1, "expired_time": 1}, {"id": 2, "status": 2},
                           {"id": 3, "status": 1, "unlimited_quota": False, "remain_quota": 0}], "total": 3}
        account = self.account("new_api", lambda request: self.response(request, data))
        self.assertEqual([key["reason"] for key in account.list_keys()], ["已过期", "已禁用", "额度已用完"])
        data["items"], data["total"] = [], 0
        self.assertEqual(account.list_keys(), [])

    def test_redirect_cancel_and_secret_redaction(self):
        calls = []
        def redirect(request):
            calls.append(request)
            return httpx.Response(302, headers={"location": "https://other.test"})
        account = self.account("new_api", redirect)
        with self.assertRaisesRegex(GatewayError, "跳转"):
            account.read_key("1")
        self.assertEqual(len(calls), 1)
        account.cancel()
        with self.assertRaisesRegex(GatewayError, "取消"):
            account.list_keys()
        self.assertEqual(len(calls), 1)
        account.secrets.update({"password", "jwt"})
        self.assertNotIn("password", account._safe("password Bearer jwt sk-abcd"))

    def test_two_accounts_on_one_host_are_isolated(self):
        one = self.account("sub2api", lambda request: self.response(request, {}, "sub2api"))
        two = self.account("sub2api", lambda request: self.response(request, {}, "sub2api"))
        one.account_id, two.account_id = "1", "2"
        key = {"id": "1", "name": "same"}
        self.assertNotEqual(one.source(key)["service_id"], two.source(key)["service_id"])
        one.client.cookies.set("session", "one")
        self.assertFalse(two.client.cookies)

    def test_metadata_only_enriches_models_visible_to_key_and_failure_is_visible(self):
        unavailable = [False]
        calls = []
        def handler(request):
            calls.append((request.method, request.url.path))
            self.assertEqual(request.headers["authorization"], "Bearer sk-model-key")
            if request.url.params.get("client_version"):
                if unavailable[0]:
                    return httpx.Response(503, request=request)
                return httpx.Response(200, json={"models": [
                    {"slug": "visible", "input_modalities": ["text", "image"], "context_window": 200000},
                    {"slug": "not-visible", "input_modalities": ["image"]}]}, request=request)
            return httpx.Response(200, json={"data": [{"id": "visible"}]}, request=request)
        account = self.account("sub2api", handler)
        records = account.discover_with_key("sk-model-key")
        self.assertEqual([record["id"] for record in records], ["visible"])
        self.assertTrue(records[0]["discovery"]["capabilities"]["supports_vision"])
        self.assertEqual(records[0]["api_protocol"], "responses")
        unavailable[0] = True
        failed = account.discover_with_key("sk-model-key")
        self.assertEqual(failed[0]["id"], "visible")
        self.assertIn("能力清单读取未完成", failed[0]["discovery"]["metadata_warning"])
        self.assertTrue(all(method == "GET" and path == "/proxy/v1/models" for method, path in calls))

    def test_saved_refresh_uses_no_login_or_key_management(self):
        from core.llm.gateway_accounts import discover_saved_gateway
        calls = []
        def handler(request):
            calls.append(request.url.path)
            self.assertNotIn("cookie", request.headers)
            self.assertEqual(request.headers["authorization"], "Bearer sk-saved")
            return httpx.Response(200, json={"data": [{"id": "chat"}]}, request=request)
        account = self.account("new_api", handler)
        with patch("core.llm.gateway_accounts.GatewayAccount", return_value=account):
            records = discover_saved_gateway({"platform": "new_api"}, "sk-saved", base_url=account.site_url)
        self.assertEqual(records[0]["id"], "chat")
        self.assertEqual(calls, ["/proxy/v1/models"])
        self.assertTrue(account.client.is_closed)

    def test_browser_challenge_and_disabled_password_do_not_submit_credentials(self):
        for public in ({"version": "new", "turnstile_check": True},
                       {"version": "new", "password_login_enabled": False}):
            calls = []
            def handler(request):
                calls.append(request.method)
                return self.response(request, public)
            account = self.account("new_api", handler)
            with self.assertRaises(GatewayError) as caught:
                account.login("u", "p")
            self.assertEqual(caught.exception.code, "browser_required")
            self.assertEqual(calls, ["GET"])


class GatewayCatalogTests(unittest.TestCase):
    def test_capabilities_are_explicit_and_do_not_infer_from_model_name_or_group(self):
        unknown = catalog_record({"id": "gpt-6-image-thinking", "allow_image_generation": True, "max_reasoning_effort": "high"})
        self.assertEqual(unknown["discovery"]["capabilities"], {})
        self.assertEqual(unknown["discovery"]["protocol_status"], "unknown")
        unsupported = catalog_record({"id": "image", "supported_endpoint_types": ["image-generation"]})
        self.assertTrue(unsupported["discovery"]["unsupported_reason"])
        supported = catalog_record({"id": "anything", "supported_endpoint_types": ["openai", "openai-response", "anthropic"],
                                    "capabilities": {"supports_vision": False, "responses_image_generation": True},
                                    "supported_reasoning_efforts": ["high", "max"]})
        model = new_catalog_model(supported, "channel", "https://site/v1")
        self.assertEqual(model["api_protocol"], "responses")
        self.assertFalse(model["supports_vision"])
        self.assertTrue(model["supports_image_generation"])
        self.assertEqual(model["reasoning_efforts"], ["high", "max"])

    def test_refresh_keeps_ids_manual_choices_and_missing_models(self):
        first = new_catalog_model(catalog_record({"id": "a", "supports_vision": True}), "c", "https://site/v1")
        second = new_catalog_model(catalog_record({"id": "b"}), "c", "https://site/v1")
        first["supports_vision"] = False
        first["discovery"]["manual_fields"] = ["supports_vision"]
        identity = first["id"]
        reconcile_catalog([first, second], [catalog_record({"id": "a", "supports_vision": True})])
        self.assertEqual(first["id"], identity)
        self.assertFalse(first["supports_vision"])
        self.assertEqual(first["discovery"]["manual_fields"], ["supports_vision"])
        self.assertEqual(second["discovery"]["availability"], "not_returned")
        reconcile_catalog([first], [{"id": "a"}])
        self.assertEqual(first["discovery"]["availability"], "listed")
        legacy = {"id": "legacy", "model_name": "old", "api_protocol": "responses", "supports_vision": True}
        reconcile_catalog([legacy], [])
        self.assertEqual(legacy["discovery"]["availability"], "not_returned")
        self.assertTrue(legacy["supports_vision"])
        self.assertIn("api_protocol", legacy["discovery"]["manual_fields"])

    def test_source_whitelist_drops_login_credentials(self):
        source = normalize_gateway_source({"platform": "new_api", "site_url": "https://site", "password": "p", "cookie": "c", "access_token": "t"})
        self.assertNotIn("password", source)
        self.assertNotIn("cookie", source)
        self.assertNotIn("access_token", source)

    def test_sub2api_manifest_fields_are_explicit(self):
        record = catalog_record({"slug": "arbitrary", "supported_endpoint_types": ["responses"],
                                 "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                                 "input_modalities": ["text", "image"], "context_window": 128000,
                                 "model_instructions": "must never reach prompts"})
        model = new_catalog_model(record, "c", "https://site/v1")
        self.assertTrue(model["supports_vision"])
        self.assertEqual(model["reasoning_efforts"], ["low", "high"])
        self.assertEqual(model["context_window_tokens"], 128000)
        self.assertNotIn("model_instructions", json.dumps(model))


if __name__ == "__main__":
    unittest.main()
