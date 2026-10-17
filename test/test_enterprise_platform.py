"""Behavioral contracts for the enterprise server, without real user data."""
import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
from sqlalchemy import select

from web.platform.server.app import create_app
from web.platform.server.config import Settings
from web.platform.server.database import AuthSource, ClientRelease, LoginTransaction, Member, Model, Question
from web.platform.server.releases import REQUIRED_FILES
from web.platform.server.security import challenge, digest


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.requests = []
        self.upstream_failure = False
        self.oauth_subject = "admin-subject"
        self.oauth_error = False
        self.token_requests = []

        def upstream(request):
            if request.url.path == "/token":
                self.token_requests.append(parse_qs(request.content.decode()))
                body = {"access_token": "upstream-token", "uid": self.oauth_subject, "expires_in": "3600"}
                if self.oauth_error:
                    body["errcode"] = 1001
                return httpx.Response(200, json=body)
            if request.url.path == "/userinfo":
                self.assertEqual(request.url.params["access_token"], "upstream-token")
                return httpx.Response(200, json={"displayName": "测试员工", "mail": "test@example.invalid"})
            self.requests.append((request, json.loads(request.content)))
            if self.upstream_failure:
                return httpx.Response(503, json={"error": "vendor secret should never be exposed"})
            if json.loads(request.content).get("stream"):
                return httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":null}]}\n\ndata: [DONE]\n\n',
                                      headers={"Content-Type": "text/event-stream"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

        database_url = "sqlite:///" + str(Path(self.temp.name) / "platform.sqlite3")
        self.pg_schema = None
        if os.environ.get("COWORK_TEST_DATABASE_URL"):
            from sqlalchemy import create_engine, text
            import uuid
            self.pg_schema = "cowork_test_" + uuid.uuid4().hex
            self.pg_engine = create_engine(os.environ["COWORK_TEST_DATABASE_URL"])
            with self.pg_engine.begin() as db:
                db.execute(text('CREATE SCHEMA "' + self.pg_schema + '"'))
            separator = "&" if "?" in os.environ["COWORK_TEST_DATABASE_URL"] else "?"
            database_url = os.environ["COWORK_TEST_DATABASE_URL"] + separator + urlencode({"options": "-csearch_path=" + self.pg_schema})

            def clean_schema():
                with self.pg_engine.begin() as db:
                    db.execute(text('DROP SCHEMA "' + self.pg_schema + '" CASCADE'))
                self.pg_engine.dispose()
            self.addCleanup(clean_schema)
        self.settings = Settings(database_url=database_url, data_dir=Path(self.temp.name), platform_id="test-company",
                                 platform_name="测试企业", public_url="http://testserver.invalid".replace("testserver.invalid", "localhost"),
                                 encryption_key=Fernet.generate_key().decode(), development=True,
                                 bootstrap_provider="company", bootstrap_subject="admin-subject")
        self.app = create_app(self.settings, transport=httpx.MockTransport(upstream))
        self.client = self.enterContext(TestClient(self.app, base_url="http://localhost"))
        self.auth = self.app.state.auth
        self.db = self.app.state.database
        self.source = {"id": "company", "name": "企业认证", "mode": "oauth2", "enabled": True,
                       "config": {"authorize_url": "http://localhost/authorize", "token_url": "http://localhost/token",
                                  "userinfo_url": "http://localhost/userinfo", "client_id": "cowork",
                                  "client_auth": "client_secret_post", "pkce": "S256", "include_redirect_uri": False,
                                  "userinfo_token_delivery": "query", "userinfo_client_id": True,
                                  "subject_token_path": "uid", "name_path": "displayName", "email_path": "mail",
                                  "error_field": "errcode", "success_values": []}, "secrets": {"client_secret": "test-secret"}}
        self.auth.save_source(self.source, bootstrap=True)

    def web_login(self, subject="admin-subject"):
        self.oauth_subject = subject
        response = self.client.post("/api/v1/auth/transactions", json={"provider_id": "company", "mode": "web"})
        self.assertEqual(response.status_code, 200, response.text)
        started = response.json()
        launch = self.client.get(started["login_url"], follow_redirects=False)
        self.assertEqual(launch.status_code, 302, launch.text)
        params = parse_qs(urlsplit(launch.headers["location"]).query)
        response = self.client.get("/auth/callback/company", params={"state": params["state"][0], "code": "one-use-code"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        self.assertEqual(challenge(self.token_requests[-1]["code_verifier"][0]), params["code_challenge"][0])
        self.assertNotIn("redirect_uri", self.token_requests[-1])
        return self.client.get("/api/v1/me").json()

    def headers(self):
        return {"X-CSRF-Token": self.client.cookies.get("cowork_csrf", "")}

    def model(self, member_id):
        response = self.client.post("/api/v1/admin/models", headers=self.headers(), json={
            "name": "企业测试模型", "provider": "openai", "protocol": "chat_completions", "model_name": "test-model",
            "base_url": "http://localhost/v1", "api_key": "vendor-secret", "options": {}, "enabled": False})
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()
        response = self.client.post(f"/api/v1/admin/models/{item['id']}/test", headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        response = self.client.put(f"/api/v1/admin/models/{item['id']}", headers=self.headers(), json={**item, "enabled": True})
        self.assertEqual(response.status_code, 200, response.text)
        member = next(x for x in self.client.get("/api/v1/admin/members").json()["items"] if x["id"] == member_id)
        response = self.client.put(f"/api/v1/admin/members/{member_id}", headers=self.headers(), json={
            "revision": member["revision"], "model_ids": [item["id"]], "default_model_id": item["id"]})
        self.assertEqual(response.status_code, 200, response.text)
        return item["id"]

    def submission(self, text="只记录这一条新问题", submission_id="message-1", **changes):
        result = {"model": "test-model", "stream": True, "messages": [
            {"role": "system", "content": "stable system prefix"},
            {"role": "user", "content": "以前个人模型的历史，不得入库"},
            {"role": "assistant", "content": "之前的回答"}, {"role": "user", "content": text}],
            "tools": [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
            "_cowork": {"client_id": "client-installation-one", "run_id": "run-1",
                        "model_revision": 1,
                        "questions": [{"id": submission_id, "text": text, "sent_at": time.time()}]}}
        result.update(changes)
        return result

    def test_guest_and_login_do_not_upload_history_or_grant_models(self):
        self.assertEqual(self.client.get("/api/v1/platform-info").json()["question_scope"], "enterprise_models_only")
        self.assertEqual(self.client.get("/api/v1/admin/questions").status_code, 401)
        admin = self.web_login()
        self.assertEqual(admin["member"]["role"], "admin")
        self.assertEqual(admin["models"], [])
        self.client.post("/api/v1/auth/logout", headers=self.headers())
        employee = self.web_login("employee-subject")
        self.assertEqual(employee["member"]["role"], "member")
        self.assertEqual(employee["models"], [])
        self.assertEqual(self.client.get("/api/v1/admin/questions").status_code, 403)
        with self.db.transaction() as db:
            self.assertEqual(list(db.scalars(select(Question))), [])

    def test_subpath_info_assets_login_and_download_keep_public_prefix(self):
        from dataclasses import replace
        settings = replace(self.settings, database_url="sqlite:///" + str(Path(self.temp.name) / "subpath.sqlite3"),
                           data_dir=Path(self.temp.name) / "subpath", public_url="http://localhost/cowork")
        app = create_app(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})))
        with TestClient(app, base_url="http://localhost") as client:
            self.assertEqual(client.get("/cowork/api/v1/platform-info").status_code, 200)
            self.assertEqual(client.get("/cowork/assets/platform.js").status_code, 200)
            html = client.get("/cowork/downloads").text
            self.assertIn('content="/cowork"', html)
            self.assertIn('/cowork/assets/platform.css', html)
            app.state.auth.save_source(self.source, bootstrap=True)
            tx = client.post("/cowork/api/v1/auth/transactions", json={"provider_id": "company", "mode": "web"}).json()
            self.assertIn("/cowork/auth/start/", tx["login_url"])
            launch = client.get(tx["login_url"], follow_redirects=False)
            self.assertEqual(parse_qs(urlsplit(launch.headers["location"]).query)["redirect_uri"][0], "http://localhost/cowork/auth/callback/company")

    def test_csrf_and_first_admin_not_inferred_from_login_order(self):
        self.oauth_subject = "outsider-first"
        start = self.client.post("/api/v1/auth/transactions", json={"provider_id": "company", "mode": "web"}).json()
        response = self.client.get(start["login_url"], follow_redirects=False)
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        response = self.client.get("/auth/callback/company", params={"state": state, "code": "code"})
        self.assertEqual(response.status_code, 403)
        self.web_login()
        self.assertEqual(self.client.post("/api/v1/admin/models", json={}).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/auth/logout", headers={**self.headers(), "Origin": "https://other.invalid"}).status_code, 403)

    def test_model_proxy_records_only_new_questions_once_without_rewriting_prefix(self):
        admin = self.web_login()
        model_id = self.model(admin["member"]["id"])
        body = self.submission()
        for _ in range(2):
            response = self.client.post(f"/api/v1/gateway/models/{model_id}/chat/completions", json=body, headers=self.headers())
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("[DONE]", response.text)
        upstream_request, projected = self.requests[-1]
        self.assertEqual(projected, {key: value for key, value in body.items() if key != "_cowork"})
        self.assertEqual(upstream_request.headers["authorization"], "Bearer vendor-secret")
        rows = self.client.get("/api/v1/admin/questions").json()["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "只记录这一条新问题")
        self.assertAlmostEqual(rows[0]["expires_at"] - rows[0]["received_at"], 30 * 86400)
        self.assertNotIn("vendor-secret", json.dumps(self.client.get("/api/v1/me").json()))

    def test_desktop_login_assignment_sdk_stream_and_question_closed_loop(self):
        from unittest.mock import patch
        from core.enterprise_client import EnterpriseClient, EnterpriseStore, submission
        from core.llm.enterprise import EnterpriseTransport
        from core.llm.factory import LLMFactory
        self.web_login()
        crypt = Fernet(Fernet.generate_key())
        class Protector:
            protect = staticmethod(crypt.encrypt)
            unprotect = staticmethod(crypt.decrypt)
        store = EnterpriseStore(Path(self.temp.name) / "desktop", Protector())
        def platform_request(request):
            response = self.client.request(request.method, str(request.url).replace("https://localhost", "http://localhost"),
                                           content=request.content, headers=dict(request.headers), follow_redirects=False)
            return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))
        transport = httpx.MockTransport(platform_request)
        desktop = EnterpriseClient(store, transport=transport)
        connection = {"schema_version": 1, "config_version": 1, "platform_id": "test-company", "platform_name": "测试企业",
                      "platform_url": "https://localhost", "distribution_channel": "enterprise"}
        desktop.bind(connection)
        self.oauth_subject = "employee-desktop"
        def open_browser(url):
            launch = self.client.get(url, follow_redirects=False)
            params = parse_qs(urlsplit(launch.headers["location"]).query)
            callback = self.client.get("/auth/callback/company", params={"state": params["state"][0], "code": "code"})
            self.assertEqual(callback.status_code, 200)
        cancel = unittest.mock.Mock()
        cancel.wait.side_effect = [False, True]
        cancel.is_set.return_value = False
        identity = desktop.login(None, cancel, open_browser)
        self.assertEqual(identity["models"], [])
        self.model(identity["member"]["id"])
        desktop.refresh()
        profile = store.profiles()[0]
        routed = EnterpriseTransport(profile, store=store, transport=transport)
        with patch("core.llm.enterprise.EnterpriseTransport", return_value=routed):
            provider = LLMFactory.create_provider_from_profile(profile)
        question = submission(profile, "员工第一次企业提问")
        try:
            chunks = list(provider.chat_stream([{"role": "user", "content": "员工第一次企业提问"}], request_context={
                "run_id": "desktop-run", "enterprise_questions": [question]}))
            self.assertTrue(any(x.get("content") == "OK" for x in chunks), chunks)
        finally:
            provider.client.close()
        rows = self.client.get("/api/v1/admin/questions").json()["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["member_id"], identity["member"]["id"])
        self.assertEqual(rows[0]["text"], "员工第一次企业提问")
        self.assertNotIn("vendor-secret", store.path.read_bytes().decode("utf-8", errors="ignore"))

    def test_recorded_question_survives_upstream_failure_and_conflicting_retries_rejected(self):
        admin = self.web_login()
        model_id = self.model(admin["member"]["id"])
        self.upstream_failure = True
        response = self.client.post(f"/api/v1/gateway/models/{model_id}/chat/completions", json=self.submission(), headers=self.headers())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("vendor secret", response.text)
        self.assertEqual(len(self.client.get("/api/v1/admin/questions").json()["items"]), 1)
        response = self.client.post(f"/api/v1/gateway/models/{model_id}/chat/completions", json=self.submission("different"), headers=self.headers())
        self.assertEqual(response.status_code, 409)

    def test_revocation_is_checked_per_call_and_disabled_member_cannot_rejoin(self):
        admin = self.web_login()
        model_id = self.model(admin["member"]["id"])
        with self.db.transaction() as db:
            db.get(Model, model_id).enabled = False
        count = len(self.requests)
        response = self.client.post(f"/api/v1/gateway/models/{model_id}/chat/completions", json=self.submission(), headers=self.headers())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(self.requests), count)
        self.client.post("/api/v1/auth/logout", headers=self.headers())
        employee = self.web_login("employee")
        with self.db.transaction() as db:
            db.get(Member, employee["member"]["id"]).enabled = False
        self.assertEqual(self.client.get("/api/v1/me").status_code, 403)

    def test_expiry_is_enforced_before_cleanup_and_physically_deleted(self):
        admin = self.web_login()
        model_id = self.model(admin["member"]["id"])
        self.client.post(f"/api/v1/gateway/models/{model_id}/chat/completions", json=self.submission(), headers=self.headers())
        with self.db.transaction() as db:
            question = db.scalar(select(Question))
            question.expires_at = time.time() - 1
            question_id = question.id
        self.assertEqual(self.client.get("/api/v1/admin/questions").json()["total"], 0)
        self.assertEqual(self.client.get("/api/v1/admin/questions/"+question_id).status_code, 404)
        self.assertEqual(self.app.state.cleanup(), 1)
        with self.db.transaction() as db:
            self.assertIsNone(db.get(Question, question_id))
        old = self.submission()
        old["_cowork"]["questions"][0]["sent_at"] -= 31 * 86400
        self.assertEqual(self.client.post(f"/api/v1/gateway/models/{model_id}/chat/completions", json=old, headers=self.headers()).status_code, 409)

    def test_desktop_handoff_requires_proof_and_code_and_is_one_use(self):
        verifier = "v" * 43
        tx = self.client.post("/api/v1/auth/transactions", json={"provider_id": "company", "mode": "desktop", "code_challenge": challenge(verifier)}).json()
        launch = self.client.get(tx["login_url"], follow_redirects=False)
        state = parse_qs(urlsplit(launch.headers["location"]).query)["state"][0]
        wrong = self.client.post(f"/api/v1/auth/transactions/{tx['id']}/status", json={"verifier": "x" * 43})
        self.assertEqual(wrong.status_code, 403)
        response = self.client.get("/auth/callback/company", params={"state": state, "code": "code"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get("/api/v1/me").status_code, 401)
        response = self.client.post(f"/api/v1/auth/transactions/{tx['id']}/status", json={"verifier": verifier})
        handoff = response.json()["handoff_code"]
        path = f"/api/v1/auth/transactions/{tx['id']}/exchange"
        response = self.client.post(path, json={"verifier": verifier, "handoff_code": handoff})
        self.assertEqual(response.status_code, 200, response.text)
        session_token = response.json()["access_token"]
        self.assertEqual(self.client.get("/api/v1/me", headers={"Authorization": "Bearer " + session_token}).status_code, 200)
        self.assertEqual(self.client.post(path, json={"verifier": verifier, "handoff_code": handoff}).status_code, 409)
        # Successful callback cleared its browser binding cookie; replay is denied.
        self.assertEqual(self.client.get("/auth/callback/company", params={"state": state, "code": "code"}).status_code, 403)

    def test_cancelled_desktop_login_cannot_be_restored_by_late_callback(self):
        verifier = "v" * 43
        tx = self.client.post("/api/v1/auth/transactions", json={"provider_id": "company", "mode": "desktop", "code_challenge": challenge(verifier)}).json()
        launch = self.client.get(tx["login_url"], follow_redirects=False)
        state = parse_qs(urlsplit(launch.headers["location"]).query)["state"][0]
        self.client.post(f"/api/v1/auth/transactions/{tx['id']}/cancel", json={"verifier": verifier})
        self.assertEqual(self.client.get("/auth/callback/company", params={"state": state, "code": "code"}).status_code, 409)

    def test_business_error_with_http_200_never_creates_session(self):
        self.oauth_error = True
        tx = self.client.post("/api/v1/auth/transactions", json={"provider_id": "company", "mode": "web"}).json()
        launch = self.client.get(tx["login_url"], follow_redirects=False)
        state = parse_qs(urlsplit(launch.headers["location"]).query)["state"][0]
        self.assertEqual(self.client.get("/auth/callback/company", params={"state": state, "code": "code"}).status_code, 502)
        self.assertEqual(self.client.get("/api/v1/me").status_code, 401)

    def test_other_oauth_mapping_and_signed_oidc_validate_identity(self):
        from authlib.jose import JsonWebKey, jwt
        self.web_login()
        key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
        nonce = {"value": ""}
        bad_nonce = {"enabled": False}

        def upstream(request):
            if request.url.path == "/keys":
                return httpx.Response(200, json={"keys": [key.as_dict(is_private=False)]})
            if request.url.path == "/oidc-token":
                claims = {"iss": "https://second.example/", "sub": "stable-second", "aud": "second-client",
                          "exp": int(time.time()) + 300, "iat": int(time.time()),
                          "nonce": "bad" if bad_nonce["enabled"] else nonce["value"]}
                return httpx.Response(200, json={"access_token": "second-token", "expires_in": 300,
                    "id_token": jwt.encode({"alg": "RS256"}, claims, key).decode()})
            if request.url.path == "/mapped-token":
                self.assertEqual(json.loads(request.content)["client_id"], "mapped-client")
                return httpx.Response(200, json={"payload": {"token": "mapped-token", "ttl": 300}})
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.headers["authorization"], "Bearer mapped-token")
            return httpx.Response(200, json={"person": {"uuid": "stable-second", "label": "映射用户"}})

        self.app.state.http._transport = httpx.MockTransport(upstream)
        sources = [
            {"id": "second", "name": "OIDC", "mode": "oidc", "enabled": False,
             "config": {"issuer": "https://second.example/", "authorize_url": "https://second.example/authorize",
                        "token_url": "https://second.example/oidc-token", "jwks_url": "https://second.example/keys", "client_id": "second-client"},
             "secrets": {"client_secret": "second-secret"}},
            {"id": "mapped", "name": "另一种 OAuth", "mode": "oauth2", "enabled": False,
             "config": {"authorize_url": "https://mapped.example/authorize", "token_url": "https://mapped.example/mapped-token",
                        "userinfo_url": "https://mapped.example/person", "client_id": "mapped-client", "token_encoding": "json",
                        "client_auth": "client_secret_post", "userinfo_method": "POST", "subject_userinfo_path": "person.uuid",
                        "access_token_path": "payload.token", "expires_in_path": "payload.ttl", "name_path": "person.label"},
             "secrets": {"client_secret": "mapped-secret"}}]
        subjects = []
        for source in sources:
            self.auth.save_source(source)
            with self.db.transaction() as db:
                record = db.get(AuthSource, source["id"])
                record.enabled, record.verified_revision = True, record.revision
            tx = self.client.post("/api/v1/auth/transactions", json={"provider_id": source["id"], "mode": "web"}).json()
            launch = self.client.get(tx["login_url"], follow_redirects=False)
            params = parse_qs(urlsplit(launch.headers["location"]).query)
            nonce["value"] = params.get("nonce", [""])[0]
            response = self.client.get("/auth/callback/" + source["id"], params={"state": params["state"][0], "code": "code"}, follow_redirects=False)
            self.assertEqual(response.status_code, 303, response.text)
            subjects.append(self.client.get("/api/v1/me").json()["member"]["id"])
        self.assertNotEqual(*subjects)  # Same subject under different identity sources never merges.
        bad_nonce["enabled"] = True
        tx = self.client.post("/api/v1/auth/transactions", json={"provider_id": "second", "mode": "web"}).json()
        launch = self.client.get(tx["login_url"], follow_redirects=False)
        params = parse_qs(urlsplit(launch.headers["location"]).query)
        self.assertEqual(self.client.get("/auth/callback/second", params={"state": params["state"][0], "code": "code"}, follow_redirects=False).status_code, 400)

    def test_ldap_requires_certificate_validation_stable_id_and_password(self):
        from unittest.mock import MagicMock, patch
        import ssl
        self.web_login()
        source = {"id": "directory", "name": "目录", "mode": "ldap", "enabled": False,
                  "config": {"host": "directory.example", "tls": "ldaps", "base_dn": "dc=example", "user_filter": "(uid={username})",
                             "identity_attribute": "entryUUID", "bind_dn": "cn=reader"}, "secrets": {"bind_password": "query-secret"}}
        self.auth.save_source(source)
        with self.db.transaction() as db:
            record = db.get(AuthSource, "directory")
            record.enabled, record.verified_revision = True, record.revision
        entry = MagicMock()
        entry.entry_dn = "uid=person,dc=example"
        entry.__getitem__.return_value.value = "stable-directory-uuid"
        search, bind = MagicMock(), MagicMock()
        search.__enter__.return_value = search
        search.entries = [entry]
        with patch("ldap3.Tls") as tls, patch("ldap3.Server"), patch("ldap3.Connection", side_effect=[search, bind]) as connection:
            response = self.client.post("/api/v1/auth/ldap", json={"provider_id": "directory", "username": "a*)(uid=*)", "password": "employee-secret", "mode": "desktop"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(tls.call_args.kwargs["validate"], ssl.CERT_REQUIRED)
            self.assertEqual(connection.call_args_list[1].kwargs["password"], "employee-secret")
            self.assertNotIn("a*)(uid=*)", search.search.call_args.args[1])
        with patch("ldap3.Connection", side_effect=RuntimeError("bind refused")):
            response = self.client.post("/api/v1/auth/ldap", json={"provider_id": "directory", "username": "person", "password": "bad", "mode": "desktop"})
            self.assertEqual(response.status_code, 401)
            self.assertNotIn("bind refused", response.text)

    def test_desktop_entry_is_single_and_does_not_guess_between_sources(self):
        from dataclasses import replace
        payload = {"mode": "desktop", "code_challenge": challenge("v" * 43)}
        self.assertEqual(self.client.post("/api/v1/auth/transactions", json=payload).status_code, 200)
        self.auth.save_source({"id": "directory", "name": "目录", "mode": "ldap", "enabled": True,
            "config": {"host": "directory.example.invalid", "tls": "ldaps", "base_dn": "dc=example,dc=invalid",
                       "user_filter": "(uid={username})", "identity_attribute": "entryUUID"}}, bootstrap=True)
        self.assertEqual(self.client.post("/api/v1/auth/transactions", json=payload).status_code, 409)
        self.auth.settings = replace(self.settings, login_provider="company")
        self.assertEqual(self.client.post("/api/v1/auth/transactions", json=payload).status_code, 200)
        with self.db.transaction() as db:
            db.get(AuthSource, "company").enabled = False
        self.assertEqual(self.client.post("/api/v1/auth/transactions", json=payload).status_code, 404)

    def test_directory_browser_flow_keeps_username_retries_and_handoff_is_bound(self):
        from unittest.mock import AsyncMock, patch
        import re
        from web.platform.server.security import PlatformError
        self.auth.save_source({"id": "directory", "name": "目录", "mode": "ldap", "enabled": True,
            "config": {"host": "directory.example.invalid", "tls": "ldaps", "base_dn": "dc=example,dc=invalid",
                       "user_filter": "(uid={username})", "identity_attribute": "entryUUID"}}, bootstrap=True)
        with self.db.transaction() as db:
            db.get(AuthSource, "directory").verified_revision = 1
            db.get(AuthSource, "company").enabled = False
        verifier = "v" * 43
        tx = self.client.post("/api/v1/auth/transactions", json={"mode": "desktop", "code_challenge": challenge(verifier)}).json()
        path = "/auth/directory/" + tx["id"]
        self.assertEqual(self.client.get(path).status_code, 403)
        page = self.client.get(tx["login_url"])
        self.assertEqual(page.status_code, 200)
        state = re.search("name='state' value='([^']+)'", page.text).group(1)
        body = {"state": state, "username": "employee@example.invalid", "password": "typed-in-browser"}
        self.assertEqual(self.client.post(path, data={**body, "state": "wrong"}).status_code, 403)
        with patch.object(self.auth, "ldap_identity", new=AsyncMock(side_effect=PlatformError("ldap_failed", "账号密码不匹配", 401))):
            response = self.client.post(path, data=body)
            self.assertEqual(response.status_code, 401)
            self.assertIn("employee@example.invalid", response.text)
            self.assertNotIn("typed-in-browser", response.text)
        status_path = f"/api/v1/auth/transactions/{tx['id']}"
        self.assertEqual(self.client.post(status_path + "/status", json={"verifier": verifier}).json()["status"], "pending")
        with patch.object(self.auth, "ldap_identity", new=AsyncMock(return_value={"subject": "stable-id", "name": "员工", "email": ""})):
            self.assertEqual(self.client.post(path, data=body).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/me").status_code, 401)
        code = self.client.post(status_path + "/status", json={"verifier": verifier}).json()["handoff_code"]
        session = self.client.post(status_path + "/exchange", json={"verifier": verifier, "handoff_code": code}).json()
        self.assertEqual(self.client.get("/api/v1/me", headers={"Authorization": "Bearer " + session["access_token"]}).status_code, 200)
        self.assertEqual(self.client.post(path, data=body).status_code, 409)

    def test_32_streams_keep_questions_unique_and_cancellation_releases_capacity(self):
        admin = self.web_login()
        model_id = self.model(admin["member"]["id"])
        session = self.auth.session(admin["member"]["id"], "desktop")
        counts = {"active": 0, "peak": 0, "closed": 0}
        batch = {"size": 32, "ready": None}

        class SlowStream(httpx.AsyncByteStream):
            def __init__(self):
                self.started = False
            async def __aiter__(self):
                self.started = True
                counts["active"] += 1
                counts["peak"] = max(counts["peak"], counts["active"])
                if counts["active"] == batch["size"]:
                    batch["ready"].set()
                await asyncio.wait_for(batch["ready"].wait(), timeout=15)
                for _ in range(6):
                    await asyncio.sleep(0.12)
                    yield b'data: {"choices":[]}\n\n'
            async def aclose(self):
                if self.started:
                    self.started = False
                    counts["active"] -= 1
                    counts["closed"] += 1

        async def endpoint(request):
            return httpx.Response(200, stream=SlowStream(), headers={"content-type": "text/event-stream"})

        self.app.state.http._transport = httpx.MockTransport(endpoint)
        async def run():
            batch["ready"] = asyncio.Event()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(self.app), base_url="http://localhost",
                                         headers={"Authorization": "Bearer " + session["access_token"]}) as client:
                path = f"/api/v1/gateway/models/{model_id}/chat/completions"
                responses = await asyncio.gather(*(client.post(path, json=self.submission(submission_id=f"concurrent-{i}")) for i in range(32)))
                self.assertTrue(all(r.status_code == 200 for r in responses))
                batch.update(size=8, ready=asyncio.Event())
                tasks = [asyncio.create_task(client.post(path, json=self.submission(submission_id=f"cancel-{i}"))) for i in range(8)]
                deadline = time.monotonic() + 5
                while counts["active"] < 8 and time.monotonic() < deadline:
                    await asyncio.sleep(0.01)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(0.05)
        started = time.monotonic()
        self.client.portal.call(run)
        self.assertGreaterEqual(counts["peak"], 32)
        self.assertEqual(counts["active"], 0)
        self.assertEqual(self.app.state.model_slots._value, self.settings.max_model_concurrency)
        rows = self.client.get("/api/v1/admin/questions").json()
        self.assertEqual(rows["total"], 40)
        self.assertEqual(len({q["id"] for q in rows["items"]}), 40)
        print(f"\nEnterprise simulated streams: peak={counts['peak']}, requests=40, seconds={time.monotonic()-started:.2f}")

    def test_release_validation_publish_withdraw_and_immutable_config(self):
        import zipfile
        self.web_login()
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w") as archive:
            for name in REQUIRED_FILES:
                archive.writestr("deepseek-cowork/"+name, b"test build fixture")
            archive.writestr("deepseek-cowork/_internal/enterprise_capabilities.json", json.dumps({
                "supported_connection_schemas": [1], "supported_platform_api_versions": [1]}))
            archive.writestr("deepseek-cowork/_internal/config.json", json.dumps({
                "api_key": "", "base_url": "https://api.deepseek.com", "disabled_skills": []}))
        data = package.getvalue()
        report = {"app_version": "1.2.3", "zip_bytes": len(data), "zip_sha256": hashlib.sha256(data).hexdigest(),
                  "supported_connection_schemas": [1], "supported_platform_api_versions": [1]}
        release = self.client.post("/api/v1/admin/client-releases", json={"app_version": "1.2.3"}, headers=self.headers()).json()
        path = "/api/v1/admin/client-releases/"+release["id"]
        self.assertEqual(self.client.put(path+"/report", json=report, headers=self.headers()).status_code, 200)
        response = self.client.put(path+"/source", content=data, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        generation = self.app.state.releases.prepare(release["id"], self.client.get("/api/v1/me").json()["member"]["id"])
        self.app.state.releases.process(release["id"], generation)
        ready = self.client.get(path).json()
        self.assertEqual(ready["state"], "ready", ready)
        self.assertEqual(self.client.get("/api/v1/client-releases/"+release["id"]+"/download").status_code, 404)
        preview = self.client.get(path+"/download")
        with zipfile.ZipFile(io.BytesIO(preview.content)) as archive:
            connection = json.loads(archive.read("deepseek-cowork/enterprise_connection.json"))
            self.assertEqual(connection["platform_id"], "test-company")
            self.assertEqual(len(connection), 6)
            for name in REQUIRED_FILES:
                self.assertEqual(archive.read("deepseek-cowork/"+name), b"test build fixture")
        payload = {"operation_id": "publish-operation-unique", "catalog_revision": 0, "recommended": True}
        published = self.client.post(path+"/publish", headers=self.headers(), json=payload)
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(self.client.post(path+"/publish", headers=self.headers(), json=payload).json(), published.json())
        self.assertEqual(self.client.get("/api/v1/client-releases/recommended").json()["release"]["id"], release["id"])
        self.assertEqual(self.client.post(path+"/recommend", headers=self.headers(), json={"operation_id":"different-operation-id", "catalog_revision":0}).status_code, 409)
        withdrawn = self.client.post(path+"/withdraw", headers=self.headers(), json={"operation_id":"withdraw-operation-id", "catalog_revision":1,"replacement_id":"","reason":"测试撤下"})
        self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
        self.assertIsNone(self.client.get("/api/v1/client-releases/recommended").json()["release"])
        self.assertEqual(self.client.get("/api/v1/client-releases/"+release["id"]+"/download").status_code, 404)

    def test_release_delete_restore_and_dependency_guards(self):
        from web.platform.server.database import ClientRelease
        self.web_login()
        actor = self.client.get("/api/v1/me").json()["member"]["id"]
        record = self.app.state.releases.create({"app_version": "5.3.0", "arch": "x64"}, actor)
        release_id = record["id"]
        path = "/api/v1/admin/client-releases/" + release_id
        directory = self.app.state.releases.directory(release_id)
        directory.mkdir(exist_ok=True)
        package = directory / "retained.source.zip"
        package.write_bytes(b"retained archive")
        for state, stage, code in (("published", "ready", "release_published"),
                                   ("processing", "validate", "release_busy"),
                                   ("draft", "receive", "release_busy")):
            with self.db.transaction() as db:
                item = db.get(ClientRelease, release_id)
                item.state, item.stage = state, stage
            payload = {"operation_id": "delete-test-" + state, "catalog_revision": 0, "revision": 1}
            response = self.client.post(path + "/delete", headers=self.headers(), json=payload)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], code)
        with self.db.transaction() as db:
            item = db.get(ClientRelease, release_id)
            item.state, item.stage = "failed", "validate"
        payload = {"operation_id": "delete-failed-release", "catalog_revision": 0, "revision": 1}
        self.assertEqual(self.client.post(path + "/delete", json=payload).status_code, 403)
        stale = self.client.post(path + "/delete", headers=self.headers(), json={**payload, "revision": 99})
        self.assertEqual(stale.status_code, 409)
        deleted = self.client.post(path + "/delete", headers=self.headers(), json=payload)
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.client.post(path + "/delete", headers=self.headers(), json=payload).json(), deleted.json())
        self.assertEqual(self.client.get("/api/v1/admin/client-releases").json()["items"], [])
        self.assertEqual(self.client.get("/api/v1/admin/client-releases?deleted=true").json()["items"][0]["id"], release_id)
        self.assertEqual(self.client.get(path + "/download").status_code, 404)
        self.assertEqual(self.client.post(path + "/prepare", headers=self.headers()).status_code, 409)
        restored = self.client.post(path + "/restore", headers=self.headers(), json={
            "operation_id": "restore-failed-release", "catalog_revision": 1, "revision": 2})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["release"]["state"], "failed")
        self.assertFalse(restored.json()["release"]["recommended"])
        self.assertEqual(package.read_bytes(), b"retained archive")
        self.assertEqual(len(self.client.get("/api/v1/admin/client-releases").json()["items"]), 1)

    def test_archive_traversal_and_prebranded_source_are_rejected(self):
        import zipfile
        from web.platform.server.releases import inspect_archive
        from web.platform.server.security import PlatformError
        for malicious in ("deepseek-cowork/../escape", "deepseek-cowork/enterprise_connection.json", "deepseek-cowork/user_data/history.json"):
            with self.subTest(malicious=malicious):
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as archive:
                    for name in REQUIRED_FILES:
                        archive.writestr("deepseek-cowork/"+name, "fixture")
                    archive.writestr(malicious, "bad")
                with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
                    with self.assertRaises(PlatformError):
                        inspect_archive(archive, self.settings)


if __name__ == "__main__":
    unittest.main()
