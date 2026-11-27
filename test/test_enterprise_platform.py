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
import zipfile
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
from sqlalchemy import select

from web.platform.server.app import create_app
from web.platform.server.config import Settings
from web.platform.server.database import AuthSource, ClientRelease, LoginTransaction, Member, Model, Question
from web.platform.server.releases import DESKTOP_ROOT
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

    def federated_callback(self, subject, *, desktop_verifier=""):
        self.oauth_subject = subject
        payload = {"provider_id": "company", "mode": "web"}
        if desktop_verifier:
            payload.update(mode="desktop", code_challenge=challenge(desktop_verifier))
        response = self.client.post("/api/v1/auth/transactions", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        started = response.json()
        launch = self.client.get(started["login_url"], follow_redirects=False)
        self.assertEqual(launch.status_code, 302, launch.text)
        params = parse_qs(urlsplit(launch.headers["location"]).query)
        response = self.client.get("/auth/callback/company", params={"state": params["state"][0], "code": "one-use-code"}, follow_redirects=False)
        self.assertEqual(challenge(self.token_requests[-1]["code_verifier"][0]), params["code_challenge"][0])
        self.assertNotIn("redirect_uri", self.token_requests[-1])
        return started, response

    def web_login(self, subject="admin-subject"):
        _, response = self.federated_callback(subject)
        self.assertEqual(response.status_code, 303, response.text)
        return self.client.get("/api/v1/me").json()

    def desktop_exchange(self, transaction_id, verifier):
        path = f"/api/v1/auth/transactions/{transaction_id}"
        handoff = self.client.post(path + "/status", json={"verifier": verifier}).json()["handoff_code"]
        session = self.client.post(path + "/exchange", json={"verifier": verifier, "handoff_code": handoff})
        self.assertEqual(session.status_code, 200, session.text)
        return {"Authorization": "Bearer " + session.json()["access_token"]}

    def desktop_login(self, subject):
        verifier = "v" * 48
        tx, response = self.federated_callback(subject, desktop_verifier=verifier)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("cowork_session=", response.headers.get("set-cookie", ""))
        return self.desktop_exchange(tx["id"], verifier)

    def headers(self):
        return {"X-CSRF-Token": self.client.cookies.get("cowork_csrf", "")}

    def test_enterprise_capabilities_publish_and_check_versioned_member_grants(self):
        admin_path = "/api/v1/admin/capabilities"
        catalog_path = "/api/v1/capabilities"
        self.assertEqual(self.client.get(catalog_path).status_code, 401)
        self.web_login()
        with self.db.transaction() as db:
            alice = Member(provider_id="company", subject="alice", name="张晨")
            bob = Member(provider_id="company", subject="bob", name="李阳")
            db.add_all([alice, bob])
        alice_header = {"Authorization": "Bearer " + self.auth.session(alice.id, "desktop")["access_token"]}
        bob_header = {"Authorization": "Bearer " + self.auth.session(bob.id, "desktop")["access_token"]}

        def create(kind, name, config):
            response = self.client.post(admin_path, headers=self.headers(),
                                        json={"kind": kind, "name": name, "description": "经营分析", "config": config})
            self.assertEqual(response.status_code, 201, response.text)
            return response.json()

        def change(item, action, status=200, **values):
            response = self.client.post(f"{admin_path}/{item['id']}/{action}", headers=self.headers(),
                                        json={"revision": item["revision"], **values})
            self.assertEqual(response.status_code, status, response.text)
            return response.json()

        def ref(item, version=None):
            return {"id": item["id"], "kind": item["kind"], "version": version or item["version"]}

        self.assertEqual(self.client.get(admin_path, headers=alice_header).status_code, 403)
        skill = create("skill", "经营分析", {"instructions": "按月汇总销售。",
                       "files": {"references/input.md": "首行是标题。", "scripts/report.py": "total = sum([1, 2])"}})
        expert = create("expert", "经营顾问", {"instructions": "生成经营报告。", "skill_ids": [skill["id"]]})
        change(expert, "test", status=400)
        change(skill, "publish", status=400)
        skill = change(change(skill, "test"), "publish")
        self.assertEqual(self.client.get(catalog_path, headers=alice_header).json(), {"items": []})
        skill = change(skill, "grant", all_members=False, member_ids=[alice.id])
        expert = change(change(expert, "test"), "publish")
        expert = change(expert, "grant", all_members=True, member_ids=[])
        self.assertEqual({item["name"] for item in self.client.get(catalog_path, headers=alice_header).json()["items"]},
                         {"经营分析", "经营顾问"})
        bob_items = self.client.get(catalog_path, headers=bob_header).json()["items"]
        self.assertEqual([item["id"] for item in bob_items], [expert["id"]])
        self.assertFalse(bob_items[0]["available"])
        self.assertEqual(bob_items[0]["config"], {})
        self.assertEqual(self.client.post(catalog_path + "/check", headers=bob_header,
                                         json={"items": [ref(expert)]}).status_code, 403)

        path = f"{admin_path}/{skill['id']}"
        update = {"name": "季度经营分析", "description": "新版", "config": {"instructions": "按季度汇总销售。"}}
        stale = self.client.put(path, headers=self.headers(), json={**update, "revision": skill["revision"] - 1})
        self.assertEqual(stale.status_code, 409)
        saved = self.client.put(path, headers=self.headers(), json={**update, "revision": skill["revision"]})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertIn("经营分析", {item["name"] for item in self.client.get(catalog_path, headers=alice_header).json()["items"]})
        skill = change(change(saved.json(), "test"), "publish")
        self.assertEqual(skill["version"], 2)
        self.assertEqual([entry["version"] for entry in self.client.get(path).json()["versions"]], [2, 1])
        checked = self.client.post(catalog_path + "/check", headers=alice_header,
                                   json={"items": [ref(skill, 1), ref(expert)], "include_config": True})
        self.assertEqual(checked.status_code, 200, checked.text)
        self.assertEqual({item["name"] for item in checked.json()["items"]}, {"经营分析", "经营顾问"})
        self.assertEqual(next(item for item in checked.json()["items"] if item["kind"] == "skill")["config"]["instructions"],
                         "按月汇总销售。")
        change(skill, "disable")
        after_disable = self.client.get(catalog_path, headers=alice_header).json()["items"]
        self.assertEqual([item["id"] for item in after_disable], [expert["id"]])
        self.assertFalse(after_disable[0]["available"])
        self.assertEqual(self.client.post(catalog_path + "/check", headers=alice_header,
                                         json={"items": [ref(skill, 1)]}).status_code, 403)

    def test_enterprise_capability_configuration_checks_do_not_execute_scripts_or_probe_mcp(self):
        self.web_login()
        route = "/api/v1/admin/capabilities"
        for kind, config in (
            ("skill", {"files": {"scripts/../../outside.py": "print(1)"}}),
            ("skill", {"files": {"scripts/Case.py": "print(1)", "scripts/case.py": "print(2)"}}),
            ("mcp", {"url": "https://member:password@mcp.example.invalid/mcp"}),
            ("mcp", {"url": "https://mcp.example.invalid/mcp?token=test"}),
            ("mcp", {"url": "https://mcp.example.invalid/mcp", "transport": "stdio"}),
            ("mcp", {"url": "http://localhost/mcp", "auth_type": "bearer", "credential_variable": "测试凭据"}),
        ):
            with self.subTest(kind=kind, config=config):
                self.assertEqual(self.client.post(route, headers=self.headers(),
                    json={"kind": kind, "name": "测试配置", "config": config}).status_code, 400)

        for kind, config in (
            ("skill", {"instructions": "尝试执行以下脚本。", "files": {"scripts/fail.py": "raise RuntimeError('must not execute')"}}),
            ("mcp", {"url": "https://mcp.example.invalid/mcp", "auth_type": "bearer", "credential_variable": "QA_TOKEN"}),
        ):
            draft = self.client.post(route, headers=self.headers(), json={"kind": kind, "name": "配置检查", "config": config})
            self.assertEqual(draft.status_code, 201, draft.text)
            item = draft.json()
            result = self.client.post(f"{route}/{item['id']}/test", headers=self.headers(), json={"revision": item["revision"]})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertTrue(result.json()["tested"])
        self.assertEqual(self.requests, [])

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

    def test_model_grants_share_member_state_and_detect_stale_changes(self):
        owner_id = self.web_login()["member"]["id"]
        first_token = self.desktop_login("model-staff-one")
        second_token = self.desktop_login("model-staff-two")
        def me(headers):
            return self.client.get("/api/v1/me", headers=headers).json()
        first_id, second_id = (me(token)["member"]["id"] for token in (first_token, second_token))
        primary, backup = self.model(owner_id), self.model(first_id)
        url = f"/api/v1/admin/models/{primary}/grants"
        def grant(current, new, headers=None):
            return self.client.put(url, headers=self.headers() if headers is None else headers,
                                   json={"previous_member_ids":current, "member_ids":new})
        self.assertEqual(grant([owner_id], [owner_id, first_id], first_token).status_code, 403)
        self.assertEqual(grant([owner_id], [owner_id, first_id, "missing-employee"]).status_code, 409)
        self.assertEqual([model["id"] for model in me(first_token)["models"]], [backup])
        expanded = [owner_id, first_id, second_id]
        response = grant([owner_id], expanded)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(grant([owner_id], expanded).json(), response.json())
        first = me(first_token)
        self.assertEqual({model["id"] for model in first["models"]}, {primary, backup})
        self.assertEqual(first["member"]["default_model_id"], backup)
        self.assertEqual(me(second_token)["member"]["default_model_id"], primary)
        response = self.client.put(f"/api/v1/admin/members/{first_id}", headers=self.headers(),
                                   json={"revision":first["member"]["revision"], "model_ids":[primary, backup],
                                         "default_model_id":primary})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(grant(expanded, [owner_id, second_id]).status_code, 200)
        self.assertEqual(me(first_token)["member"]["default_model_id"], backup)
        self.assertEqual(grant(expanded, [owner_id]).status_code, 409)
        self.assertEqual(me(second_token)["member"]["default_model_id"], primary)

    def submission(self, text="员工新发的问题", submission_id="message-1", **changes):
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

    def test_inherited_model_access_follows_department_and_preserves_personal_grants(self):
        owner = self.web_login()["member"]["id"]
        employee = self.desktop_login("department-member")
        member = self.client.get("/api/v1/me", headers=employee).json()["member"]
        model = self.model(owner)
        org = "/api/v1/admin/departments"
        def create(name, parent=""):
            res = self.client.post(org, headers=self.headers(), json={"name": name, "parent_id": parent})
            self.assertEqual(res.status_code, 200, res.text)
            return res.json()["id"]
        top = create("产品")
        child = create("交付", top)
        grants = f"/api/v1/admin/models/{model}/grants"
        def grant(old, new, old_units, new_units):
            return self.client.put(grants, headers=self.headers(), json={"member_ids": new, "previous_member_ids": old,
                "department_ids": new_units, "previous_department_ids": old_units})
        self.assertEqual(grant([owner], [owner], [], [top]).status_code, 200)
        self.assertEqual(grant([owner], [owner], [], [child]).status_code, 409)
        def move(unit):
            current = self.client.get("/api/v1/me", headers=employee).json()["member"]
            res = self.client.put("/api/v1/admin/members/"+member["id"], headers=self.headers(),
                                  json={"revision":current["revision"], "department_id":unit})
            self.assertEqual(res.status_code, 200, res.text)
        def effective():
            return [m["id"] for m in self.client.get("/api/v1/me", headers=employee).json()["models"]]
        move(child)
        self.assertEqual(effective(), [model])
        move("")
        self.assertEqual(effective(), [])
        self.assertEqual(grant([owner], [owner,member["id"]], [top], [top]).status_code, 200)
        move(child)
        self.assertEqual(effective(), [model])
        move("")
        self.assertEqual(effective(), [model])

    def test_export_contains_all_filtered_rows_as_excel_text(self):
        from io import BytesIO
        from unittest.mock import patch
        from openpyxl import load_workbook
        owner = self.web_login()["member"]["id"]
        with self.db.transaction() as db:
            for i in range(57):
                self.db.audit(db, "export_sample", member_id=owner, target=f"=A{i}")
            self.db.audit(db, "export_sample", member_id="", target="excluded")
        params = {"member_id":owner,"keyword":"export_sample","since":time.time()-60,"until":time.time()+60}
        route = "/api/v1/admin/chat-audit"
        self.assertEqual(self.client.get(route+"/events", params=params).json()["total"], 57)
        with patch("web.platform.server.chat_audit.EXCEL_RECORDS_PER_SHEET", 25):
            response = self.client.get(route+"/export.xlsx", params={**params,"kind":"events"})
        self.assertEqual(response.status_code, 200, response.text if response.status_code != 200 else "")
        with BytesIO(response.content) as stream:
            book = load_workbook(stream,read_only=True)
            self.assertEqual(book.sheetnames, ["平台操作", "平台操作 2", "平台操作 3", "筛选条件"])
            rows = [row for sheet in book.worksheets[:-1] for row in list(sheet.rows)[1:]]
            self.assertEqual(len(rows),57)
            self.assertTrue(all(r[2].value==owner and r[4].data_type=="s" and r[4].value.startswith("=A") for r in rows))
            book.close()
        self.assertEqual(self.client.get(route+"/events", params={**params,"until":1}).status_code,400)

    def test_chat_audit_keeps_latest_per_member_and_filters_contents(self):
        from web.platform.server.chat_audit import ChatAuditRequest, RETENTION, EVENT_RETENTION
        from web.platform.server.database import AuditEvent

        administrator = self.web_login()["member"]["id"]
        model_id = self.model(administrator)
        endpoint = "/api/v1/chat-audit/latest"
        admin_endpoint = "/api/v1/admin/chat-audit"
        when = time.time_ns() // 1_000
        payload = {"conversation_id": "chat-a", "request_id": "request-a", "run_id": "run-1", "captured_us": when,
                   "protocol": "chat_completions", "model_name": "test-model", "model_source": "enterprise", "model_id": model_id,
                   "parameters": {"model": "test-model", "extra_headers": {"Authorization": "Bearer FAKE_AUTH_TOKEN"}, "messages": [
                       {"role": "user", "content": "原有文字"},
                       {"role": "user", "content": [{"type": "input_file", "filename": "private.pdf", "file_data": "BASE64_DO_NOT_KEEP"},
                                                    {"type": "text", "text": "前文 password=FAKE_SECRET"}]}]}}
        self.assertEqual(self.client.put(endpoint, headers=self.headers(), json=payload).status_code, 200)
        advanced = {**payload, "request_id": "request-b", "captured_us": when + 1}
        self.assertEqual(self.client.put(endpoint, headers=self.headers(), json=advanced).status_code, 200)
        self.assertEqual(self.client.put(endpoint, headers=self.headers(), json=payload).status_code, 200)
        listed = self.client.get(admin_endpoint, params={"member_id": administrator, "keyword": "chat-a"}).json()
        self.assertEqual(listed["total"], 1)
        self.assertNotIn("parameters", listed["items"][0])
        record_id = listed["items"][0]["id"]
        result = self.client.get(admin_endpoint + "/" + record_id)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["request_id"], "request-b")
        self.assertIn("原有文字", result.text)
        for secret in ("FAKE_AUTH_TOKEN", "FAKE_SECRET", "BASE64_DO_NOT_KEEP", "private.pdf"):
            self.assertNotIn(secret, result.text)
        saved = result.json()
        self.assertAlmostEqual(saved["expires_at"] - saved["captured_at"], RETENTION)
        self.assertIn("chat_audit.read", [event["action"] for event in self.client.get(admin_endpoint + "/events").json()["items"]])

        gateway = self.submission()
        gateway["_cowork"]["chat_audit_version"] = 1
        response = self.client.post(f"/api/v1/gateway/models/{model_id}/chat/completions", headers=self.headers(), json=gateway)
        self.assertEqual(response.status_code, 200, response.text)
        with self.db.transaction() as db:
            self.assertIsNone(db.scalar(select(Question)))
        self.assertNotIn("_cowork", self.requests[-1][1])
        self.assertEqual(self.requests[-1][1]["messages"], gateway["messages"])

        now = time.time()
        with self.db.transaction() as db:
            db.get(ChatAuditRequest, record_id).expires_at = now - 1
            db.add(AuditEvent(action="stale.test", created_at=now - EVENT_RETENTION - 1))
        self.assertEqual(self.client.get(admin_endpoint + "/" + record_id).status_code, 404)
        self.app.state.cleanup()
        with self.db.transaction() as db:
            self.assertIsNone(db.get(ChatAuditRequest, record_id))
            self.assertIsNone(db.scalar(select(AuditEvent).where(AuditEvent.action == "stale.test")))

    def test_personal_model_audit_is_opt_in_and_permit_is_scoped(self):
        from unittest.mock import patch

        self.web_login()
        policy_url = "/api/v1/admin/chat-audit/policy"
        client_url = "/api/v1/chat-audit/policy"
        latest = "/api/v1/chat-audit/latest"
        run = {"conversation_id": "chat-b", "run_id": "run-1", "space_id": "space-a"}
        self.assertEqual(self.client.post(client_url, headers=self.headers(), json=run).json(), {"enabled": False})
        policy = self.client.get(policy_url).json()
        self.assertFalse(policy["personal_enabled"])
        enabled = self.client.put(policy_url, headers=self.headers(), json={**policy, "personal_enabled": True})
        self.assertEqual(enabled.status_code, 200, enabled.text)
        self.assertEqual(self.client.post(client_url, headers=self.headers(), json={"conversation_id": "chat-b", "run_id": "run-1"}).status_code, 403)
        with patch.object(self.app.state.team_spaces, "policy", return_value={"allowed": True}) as authorize:
            permission = self.client.post(client_url, headers=self.headers(), json=run).json()
            self.assertTrue(permission["enabled"])
            self.assertEqual(authorize.call_args.args[1:], ("space-a", "personal"))
            disabled = self.client.put(policy_url, headers=self.headers(), json={**enabled.json(), "personal_enabled": False})
            self.assertEqual(disabled.status_code, 200, disabled.text)
            self.assertEqual(self.client.post(client_url, headers=self.headers(), json=run).json(), {"enabled": False})
        retained = {"conversation_id": run["conversation_id"], "run_id": run["run_id"], "request_id": "request-a",
                    "model_source": "personal", "model_name": "a-self-model", "protocol": "responses",
                    "captured_us": time.time_ns() // 1_000, "parameters": {"input": "安全的文本"}, "permit": permission["permit"]}
        self.assertEqual(self.client.put(latest, headers=self.headers(), json={**retained, "run_id": "different-run"}).status_code, 403)
        self.assertEqual(self.client.put(latest, headers=self.headers(), json=retained).status_code, 200)
        self.client.post("/api/v1/auth/logout", headers=self.headers())
        employee_headers = self.desktop_login("another-user")
        self.assertEqual(self.client.put(latest, headers=employee_headers, json=retained).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/admin/chat-audit", headers=employee_headers).status_code, 403)

    def test_guest_and_login_do_not_upload_history_or_grant_models(self):
        self.assertEqual(self.client.get("/api/v1/platform-info").json()["chat_audit"]["retention_days"], 30)
        self.assertEqual(self.client.get("/api/v1/admin/chat-audit").status_code, 401)
        admin = self.web_login()
        self.assertEqual(admin["member"]["role"], "admin")
        self.assertEqual(admin["models"], [])
        self.client.post("/api/v1/auth/logout", headers=self.headers())
        tx, denied = self.federated_callback("employee-subject")
        self.assertEqual(denied.status_code, 403)
        self.assertIn("网页版仅供平台管理员登录", denied.text)
        self.assertNotIn("cowork_session=", denied.headers.get("set-cookie", ""))
        self.assertEqual(self.client.get("/api/v1/me").status_code, 401)
        self.assertEqual(self.client.get("/downloads").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/client-releases/recommended?app_layout=electron").status_code, 200)
        employee_headers = self.desktop_login("employee-subject")
        employee = self.client.get("/api/v1/me", headers=employee_headers).json()
        self.assertEqual(employee["member"]["role"], "member")
        self.assertEqual(employee["models"], [])
        self.assertEqual(self.client.get("/api/v1/admin/chat-audit", headers=employee_headers).status_code, 403)
        with self.db.transaction() as db:
            self.assertEqual(list(db.scalars(select(Question))), [])
            denied_tx = db.get(LoginTransaction, tx["id"])
            self.assertEqual((denied_tx.status, denied_tx.error), ("failed", "web_admin_required"))

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

    def test_model_proxy_ignores_legacy_questions_without_rewriting_prefix(self):
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
        with self.db.transaction() as db:
            self.assertIsNone(db.scalar(select(Question)))
        self.assertNotIn("vendor-secret", json.dumps(self.client.get("/api/v1/me").json()))

    def test_desktop_login_assignment_and_legacy_sdk_stream_without_question_records(self):
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
        self.assertEqual(self.requests[-1][1]["messages"], [{"role": "user", "content": "员工第一次企业提问"}])
        self.assertNotIn("_cowork", self.requests[-1][1])
        with self.db.transaction() as db:
            self.assertIsNone(db.scalar(select(Question)))
        self.assertNotIn("vendor-secret", store.path.read_bytes().decode("utf-8", errors="ignore"))

    def test_upstream_failure_remains_visible_without_recording_or_validating_legacy_questions(self):
        admin = self.web_login()
        model_id = self.model(admin["member"]["id"])
        self.upstream_failure = True
        response = self.client.post(f"/api/v1/gateway/models/{model_id}/chat/completions", json=self.submission(), headers=self.headers())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("vendor secret", response.text)
        self.upstream_failure = False
        retry = self.submission("different")
        retry["_cowork"]["questions"][0]["sent_at"] -= 31 * 86400
        response = self.client.post(f"/api/v1/gateway/models/{model_id}/chat/completions", json=retry, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("[DONE]", response.text)
        self.assertEqual(self.requests[-1][1], {key: value for key, value in retry.items() if key != "_cowork"})
        with self.db.transaction() as db:
            self.assertIsNone(db.scalar(select(Question)))

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
        employee_headers = self.desktop_login("employee")
        employee = self.client.get("/api/v1/me", headers=employee_headers).json()
        with self.db.transaction() as db:
            db.get(Member, employee["member"]["id"]).enabled = False
        self.assertEqual(self.client.get("/api/v1/me", headers=employee_headers).status_code, 403)

    def test_obsolete_question_apis_are_removed_and_existing_records_expire_on_schedule(self):
        admin = self.web_login()
        model_id = self.model(admin["member"]["id"])
        now = time.time()
        with self.db.transaction() as db:
            for identifier, expiration in (("expired", now - 1), ("retained", now + 60)):
                db.add(Question(id=identifier, member_id=admin["member"]["id"], client_id="legacy-client",
                                submission_id=identifier, model_id=model_id, body="旧提问",
                                received_at=expiration - 30 * 86400, expires_at=expiration))
        self.assertEqual(self.client.get("/api/v1/admin/questions").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/admin/questions/retained").status_code, 404)
        self.assertEqual(self.app.state.cleanup(), 1)
        with self.db.transaction() as db:
            self.assertIsNone(db.get(Question, "expired"))
            self.assertIsNotNone(db.get(Question, "retained"))

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
        verifier = "w" * 48
        for source in sources:
            self.auth.save_source(source)
            with self.db.transaction() as db:
                record = db.get(AuthSource, source["id"])
                record.enabled, record.verified_revision = True, record.revision
            tx = self.client.post("/api/v1/auth/transactions", json={"provider_id": source["id"], "mode": "desktop",
                                  "code_challenge": challenge(verifier)}).json()
            launch = self.client.get(tx["login_url"], follow_redirects=False)
            params = parse_qs(urlsplit(launch.headers["location"]).query)
            nonce["value"] = params.get("nonce", [""])[0]
            response = self.client.get("/auth/callback/" + source["id"], params={"state": params["state"][0], "code": "code"}, follow_redirects=False)
            self.assertEqual(response.status_code, 200, response.text)
            employee_headers = self.desktop_exchange(tx["id"], verifier)
            subjects.append(self.client.get("/api/v1/me", headers=employee_headers).json()["member"]["id"])
        self.assertNotEqual(*subjects)  # Same subject under different identity sources never merges.
        bad_nonce["enabled"] = True
        tx = self.client.post("/api/v1/auth/transactions", json={"provider_id": "second", "mode": "web"}).json()
        launch = self.client.get(tx["login_url"], follow_redirects=False)
        params = parse_qs(urlsplit(launch.headers["location"]).query)
        self.assertEqual(self.client.get("/auth/callback/second", params={"state": params["state"][0], "code": "code"}, follow_redirects=False).status_code, 400)

    def test_ldap_requires_certificate_validation_stable_id_and_password(self):
        from unittest.mock import AsyncMock, MagicMock, patch
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
        identity = {"subject": "stable-directory-uuid", "name": "目录员工", "email": ""}
        with patch.object(self.auth, "ldap_identity", new=AsyncMock(return_value=identity)):
            denied = self.client.post("/api/v1/auth/ldap", json={"provider_id": "directory", "username": "person",
                                      "password": "employee-secret", "mode": "web"})
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(denied.json()["error"]["code"], "web_admin_required")
            self.assertNotIn("cowork_session=", denied.headers.get("set-cookie", ""))
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

    def test_32_streams_cancel_cleanly_without_writing_legacy_questions(self):
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
        with self.db.transaction() as db:
            self.assertIsNone(db.scalar(select(Question)))
        print(f"\nEnterprise simulated streams: peak={counts['peak']}, requests=40, seconds={time.monotonic()-started:.2f}")

    def desktop_package(self, version="99.0.0", extra=None, *, root=DESKTOP_ROOT):
        from web.platform.server.releases import DESKTOP_REQUIRED_FILES
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w") as archive:
            for name in DESKTOP_REQUIRED_FILES - {"desktop-release.json"}:
                archive.writestr(root + "/" + name, b"fixture")
            archive.writestr(root + "/desktop-release.json", json.dumps({
                "schema_version": 1, "app_layout": "electron", "target": "windows-x64", "version": version}))
            archive.writestr(root + "/resources/service/_internal/enterprise_capabilities.json", json.dumps({
                "supported_connection_schemas": [1], "supported_platform_api_versions": [1]}))
            for name, contents in (extra or {}).items():
                archive.writestr(root + "/" + name, contents)
        data = package.getvalue()
        return data, {"schema_version": 1, "version": version, "app_layout": "electron", "target_os": "windows", "arch": "x64",
            "filename": f"cowork-desktop-v{version}-windows-x64.zip", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "notes": "测试更新"}

    def test_desktop_release_import_is_pinned_and_manifest_matches_enterprise_zip(self):
        from unittest.mock import patch
        from web.platform.server import releases as module
        self.web_login()
        data, manifest = self.desktop_package()
        seen = []
        def download(request):
            seen.append(request.url.path)
            if request.url.path.endswith("/latest/download/latest-desktop-windows-x64.json"):
                return httpx.Response(200, json=manifest)
            if request.url.path.endswith("/download/v99.0.0/" + manifest["filename"]):
                return httpx.Response(200, content=data)
            return httpx.Response(404)
        original = httpx.AsyncClient
        actor = self.client.get("/api/v1/me").json()["member"]["id"]
        async def run():
            item, selected = await self.app.state.releases.github_candidate(actor)
            await self.app.state.releases.import_github(item["id"], selected, actor, item["generation"])
            return item
        with patch.object(module.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(download), **kw)):
            item = asyncio.run(run())
        path = "/api/v1/admin/client-releases/" + item["id"]
        ready = self.client.get(path).json()
        self.assertEqual(ready["state"], "ready", ready)
        self.assertFalse(ready["recommended"])
        preview = self.client.get(path + "/download")
        self.assertEqual(hashlib.sha256(preview.content).hexdigest(), ready["sha256"])
        self.assertNotEqual(ready["sha256"], manifest["sha256"])
        offline = self.client.get(path + "/manifest")
        self.assertIn('latest-desktop-windows-x64.json', offline.headers['content-disposition'])
        self.assertEqual(offline.json()["sha256"], ready["sha256"])
        self.assertEqual(offline.json()["size"], len(preview.content))
        public_path = "/api/v1/client-releases/" + item["id"]
        self.assertEqual(self.client.get(public_path + "/manifest").status_code, 404)
        # Exercise the same local ZIP boundary used by an offline employee.
        from core.updater import prepare_desktop_local
        from threading import Event
        root = Path(self.temp.name) / "offline"
        root.mkdir()
        package_path = root / offline.json()["filename"]
        package_path.write_bytes(preview.content)
        (root / "latest-desktop-windows-x64.json").write_text(json.dumps(offline.json()), encoding="utf-8")
        prepared = prepare_desktop_local(str(package_path), str(root / "cache"), "1.0.0", ready["connection"], Event(), None)
        self.assertTrue(prepared["manifest_verified"])
        published = self.client.post(path + "/publish", headers=self.headers(), json={
            "operation_id": "publish-desktop-import", "catalog_revision": 0, "recommended": True})
        self.assertEqual(published.status_code, 200, published.text)
        self.assertEqual(self.client.get("/api/v1/client-releases/recommended?app_layout=electron").json()["release"]["id"], item["id"])
        self.assertEqual(self.client.get("/api/v1/client-releases/recommended").json()["release"]["id"], item["id"])
        self.assertEqual(self.client.get(public_path + "/manifest").json(), offline.json())
        self.assertEqual(self.client.get("/api/v1/client-releases/recommended?app_layout=qt").status_code, 410)
        withdrawn = self.client.post(path + "/withdraw", headers=self.headers(), json={
            "operation_id": "withdraw-desktop-import", "catalog_revision": 1, "replacement_id": "", "reason": "撤回测试"})
        self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
        self.assertIsNone(self.client.get("/api/v1/client-releases/recommended?app_layout=electron").json()["release"])
        self.assertEqual(self.client.get("/api/v1/client-releases/" + item["id"] + "/download").status_code, 404)
        self.assertEqual(self.client.get(public_path + "/manifest").status_code, 404)
        self.assertEqual(len(seen), 2)

    def test_v2_migration_retires_legacy_release_and_preserves_members(self):
        from sqlalchemy import text
        from web.platform.server.database import Platform
        self.web_login()
        actor = self.client.get("/api/v1/me").json()["member"]["id"]
        release = self.app.state.releases.create({"app_version": "1.2.3"}, actor)
        with self.db.transaction() as db:
            record = db.get(ClientRelease, release["id"])
            record.state, record.recommended = "published", True
            db.get(Platform, 1).schema_version = 2
        with self.db.engine.begin() as db:
            db.execute(text("ALTER TABLE client_releases DROP COLUMN app_layout"))
        self.db.migrate(self.settings)
        self.db.migrate(self.settings)
        self.assertIsNone(self.client.get("/api/v1/client-releases/recommended").json()["release"])
        self.assertIsNone(self.client.get("/api/v1/client-releases/recommended?app_layout=electron").json()["release"])
        with self.db.transaction() as db:
            self.assertEqual(db.get(Platform, 1).schema_version, 6)
            self.assertEqual(db.get(Platform, 1).catalog_revision, 1)
            self.assertIsNotNone(db.get(Member, actor))
            record = db.get(ClientRelease, release["id"])
            self.assertEqual((record.state, record.recommended, record.stage), ("withdrawn", False, "retired"))
        self.assertEqual(self.client.get("/api/v1/admin/client-releases").json()["items"], [])
        self.assertEqual(self.client.post("/api/v1/admin/client-releases", headers=self.headers(), json={
            "app_version": "1.2.4", "app_layout": "qt"}).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/admin/client-releases/" + release["id"] + "/publish", headers=self.headers(), json={
            "operation_id": "republish-retired-qt", "catalog_revision": 1}).status_code, 410)

    def test_v3_migration_preserves_electron_recommendation_and_business_data(self):
        from web.platform.server.database import Platform
        self.web_login()
        actor = self.client.get("/api/v1/me").json()["member"]["id"]
        model = self.model(actor)
        with self.db.transaction() as db:
            old = ClientRelease(app_version="1.0.0", app_layout="qt", state="published", recommended=True, created_by=actor)
            current = ClientRelease(app_version="2.0.0", state="published", recommended=True, created_by=actor)
            db.add_all([old, current]); db.flush()
            current_id, old_id = current.id, old.id
            db.get(Platform, 1).schema_version = 3
        self.db.migrate(self.settings)
        self.db.migrate(self.settings)
        self.assertEqual(self.client.get("/api/v1/client-releases/recommended").json()["release"]["id"], current_id)
        with self.db.transaction() as db:
            self.assertIsNotNone(db.get(Model, model))
            self.assertEqual(db.get(Member, actor).role, "admin")
            self.assertFalse(db.get(ClientRelease, old_id).recommended)
            self.assertEqual(db.get(Platform, 1).catalog_revision, 1)

    def test_v5_migration_preserves_existing_direct_authorization(self):
        from sqlalchemy import text
        from web.platform.server.database import Department, ModelUsageDay, Platform

        actor = self.web_login()["member"]["id"]
        model = self.model(actor)
        with self.db.transaction() as db:
            db.get(Platform, 1).schema_version = 5
        with self.db.engine.begin() as conn:
            Department.__table__.drop(conn)
            ModelUsageDay.__table__.drop(conn)
            for table, column in (("members", "department_id"), ("models", "department_ids"),
                                  ("enterprise_capabilities", "department_ids")):
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
        self.db.migrate(self.settings)
        self.db.migrate(self.settings)
        session = self.client.get("/api/v1/me").json()
        self.assertEqual(session["member"]["department_id"], "")
        self.assertEqual([m["id"] for m in session["models"]], [model])
        self.assertEqual(self.client.get("/api/v1/admin/models").json()["items"][0]["department_ids"], [])

    def test_v4_migration_creates_capability_tables_without_retiring_releases_again(self):
        from web.platform.server.capabilities import Capability, CapabilityVersion
        from web.platform.server.database import Platform
        actor = self.web_login()["member"]["id"]
        with self.db.transaction() as db:
            old = ClientRelease(app_version="1.0.0", app_layout="qt", state="withdrawn",
                                recommended=False, stage="retired", created_by=actor, revision=3)
            db.add(old)
            db.flush()
            old_id = old.id
            platform = db.get(Platform, 1)
            platform.schema_version, platform.catalog_revision = 4, 2
        with self.db.engine.begin() as connection:
            CapabilityVersion.__table__.drop(connection)
            Capability.__table__.drop(connection)
        self.db.migrate(self.settings)
        self.assertEqual(self.client.get("/healthz").json()["schema_version"], 6)
        self.assertEqual(self.client.get("/api/v1/admin/capabilities").json()["items"], [])
        with self.db.transaction() as db:
            self.assertEqual(db.get(Platform, 1).catalog_revision, 2)
            self.assertEqual(db.get(ClientRelease, old_id).revision, 3)

    def test_cancelled_github_import_cannot_overwrite_a_new_upload(self):
        from unittest.mock import patch
        from web.platform.server import releases as module
        self.web_login()
        _, manifest = self.desktop_package()
        actor = self.client.get("/api/v1/me").json()["member"]["id"]
        original = httpx.AsyncClient
        async def run():
            with patch.object(module.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=manifest)), **kw)):
                item, selected = await self.app.state.releases.github_candidate(actor)
            self.app.state.releases.cancel(item["id"], actor)
            with self.db.transaction() as db:
                record = db.get(ClientRelease, item["id"])
                record.stage, record.error = "received", "new upload"
            with patch.object(module.httpx, "AsyncClient", side_effect=lambda **kw: original(transport=httpx.MockTransport(
                    lambda request: httpx.Response(503)), **kw)):
                await self.app.state.releases.import_github(item["id"], selected, actor, item["generation"])
            return item
        item = asyncio.run(run())
        current = self.client.get("/api/v1/admin/client-releases/" + item["id"]).json()
        self.assertEqual((current["state"], current["stage"], current["error"]), ("draft", "received", "new upload"))

    def test_release_validation_publish_withdraw_and_immutable_config(self):
        import zipfile
        from scripts.package_release import ARCHIVE_ROOT
        self.web_login()
        data, report = self.desktop_package("1.2.3", {"resources/service/_internal/config.json": json.dumps({
            "api_key": "", "base_url": "https://api.deepseek.com", "disabled_skills": []})}, root=ARCHIVE_ROOT)
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
            connection = json.loads(archive.read(ARCHIVE_ROOT + "/enterprise_connection.json"))
            self.assertEqual(connection["platform_id"], "test-company")
            self.assertEqual(len(connection), 6)
            with zipfile.ZipFile(io.BytesIO(data)) as source:
                for name in source.namelist():
                    self.assertEqual(archive.read(name), source.read(name))
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
        for malicious in ("../escape", "enterprise_connection.json", "user_data/history.json"):
            with self.subTest(malicious=malicious):
                data, _ = self.desktop_package(extra={malicious: "bad"})
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    with self.assertRaises(PlatformError):
                        inspect_archive(archive, self.settings)

        from scripts.package_release import ARCHIVE_ROOT
        data, _ = self.desktop_package(root=ARCHIVE_ROOT)
        mixed = io.BytesIO(data)
        with zipfile.ZipFile(mixed, "a") as archive:
            archive.writestr(DESKTOP_ROOT + "/extra.txt", "conflicting archive directories")
        with zipfile.ZipFile(mixed) as archive:
            with self.assertRaises(PlatformError):
                inspect_archive(archive, self.settings)


if __name__ == "__main__":
    unittest.main()
