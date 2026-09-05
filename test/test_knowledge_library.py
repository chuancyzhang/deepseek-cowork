import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import requests

from core.knowledge_library import (KnowledgeError, KnowledgeService, KnowledgeStore,
                                    knowledge_script_call, response_data, service_url)
from core.clarify_mode import normalize_run_context


class TestProtector:
    __test__ = False

    def protect(self, value):
        return b"encrypted:" + value[::-1]

    def unprotect(self, value):
        return value[len(b"encrypted:"):][::-1]


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


class WeKnoraFixture:
    """Wire shapes from the installed WeKnora handlers, not a second KB implementation."""

    def __init__(self):
        self.calls = []
        self.documents = {"doc-a": {"id": "doc-a", "knowledge_base_id": "kb-a", "title": "权限设计", "parse_status": "completed"},
                          "doc-b": {"id": "doc-b", "knowledge_base_id": "kb-b", "title": "其他资料", "parse_status": "completed"}}
        self.fail_status = None
        self.fail_network = False
        self.expired = False

    def request(self, method, url, **kwargs):
        path = url.split("/api/v1/")[1]
        self.calls.append((method, path, copy.deepcopy({k: v for k, v in kwargs.items() if k != "files"})))
        if path == "auth/login":
            return Response({"success": True, "token": "secret-token", "refresh_token": "secret-refresh",
                             "user": {"id": "user-a", "email": "reader@example.test"}, "tenant": {"id": 1}})
        if path == "auth/refresh":
            self.expired = False
            return Response({"success": True, "access_token": "new-token", "refresh_token": "new-refresh"})
        if self.expired:
            return Response({}, 401)
        if self.fail_status:
            return Response({}, self.fail_status)
        if self.fail_network:
            raise requests.ConnectionError("network lost")
        if path == "auth/me":
            return Response({"success": True, "data": {"memberships": [{"tenant_id": 1, "tenant_name": "产品"}, {"tenant_id": 2, "tenant_name": "研究"}]}})
        if path == "auth/logout":
            return Response({"success": True})
        if path == "knowledge-bases":
            creator = kwargs.get("params", {}).get("creator")
            data = [{"id": "kb-a", "name": "产品资料", "creator_id": "user-a"}] if creator == "mine" else []
            return Response({"success": True, "data": data})
        if path == "organizations":
            return Response({"success": True, "data": {"organizations": [{"id": "org-a", "name": "共享研究"}], "total": 1}})
        if path == "organizations/org-a/shared-knowledge-bases":
            return Response({"success": True, "data": [{"knowledge_base": {"id": "kb-b", "name": "共享研究资料"}, "permission": "viewer"}]})
        if path in ("knowledge-bases/kb-a", "knowledge-bases/kb-b"):
            return Response({"success": True, "data": {"id": path.split("/")[-1]}})
        if path.startswith("knowledge/"):
            item = self.documents.get(path.split("/")[-1])
            return Response({"success": True, "data": item}, 200 if item else 404)
        if path == "knowledge-search":
            return Response({"success": True, "data": [{"knowledge_id": "doc-a", "knowledge_base_id": "kb-a", "knowledge_title": "权限设计", "content": "只读用户可以阅读。", "chunk_index": 0}]})
        if path.startswith("chunks/"):
            return Response({"success": True, "data": [{"id": "chunk-a", "content": "只读用户可以阅读。"}], "total": 1, "page": 1, "page_size": 20})
        if path.endswith("/knowledge/folders"):
            return Response({"success": True, "data": {"folders": [], "total_document_count": 1}})
        if path.endswith("/knowledge/file"):
            return Response({"success": True, "data": {"id": "doc-a", "parse_status": "pending"}})
        if path.endswith("/knowledge"):
            return Response({"success": True, "data": [self.documents["doc-a"]], "total": 1})
        if path.endswith("/wiki/pages"):
            return Response({"success": True, "data": {"pages": getattr(self, "wiki_pages", [{"slug": "intro", "title": "产品概览", "page_type": "index"}]), "total_pages": 1}})
        if "/wiki/pages/" in path:
            return Response({"success": True, "data": {"title": "产品概览", "content": "产品概览正文"}})
        raise AssertionError(f"Unexpected wire request: {method} {path}")


class KnowledgeLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = KnowledgeStore(self.temp.name, TestProtector())
        self.transport = WeKnoraFixture()
        self.service = KnowledgeService(self.store, self.transport)
        self.service.login("http://localhost", "reader@example.test", "password")
        self.scope = self.service.snapshot(session_id="conversation-a")
        self.ref = self.service.reference(self.scope, "kb-a", "权限设计", "doc-a")

    def selected(self, refs=None):
        return {**self.scope, "refs": refs if refs is not None else [self.ref]}

    def assertCode(self, code, call):
        with self.assertRaises(KnowledgeError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)

    def test_transport_accepts_remote_http_and_https(self):
        self.assertEqual(service_url("http://localhost/api/v1"), "http://localhost")
        self.assertEqual(service_url("http://192.168.1.20:8080"), "http://192.168.1.20:8080")
        self.assertEqual(service_url("http://team.example"), "http://team.example")
        for bad in ( "https://user:password@team.example", "https://team.example/a", "file:///a"):
            with self.assertRaises(KnowledgeError):
                service_url(bad)

    def test_password_not_saved_and_tokens_encrypted(self):
        with open(self.store.path, "rb") as stream:
            content = stream.read()
        self.assertNotIn(b"secret-token", content)
        self.assertNotIn(b"secret-refresh", content)
        self.assertNotIn(b"password", content)
        self.assertNotIn("credentials", self.scope)

    def test_document_selection_never_expands_parent_kb(self):
        self.service.tool(self.selected(), "search", {"query": "权限"})
        body = self.transport.calls[-1][2]["json"]
        self.assertEqual(body, {"query": "权限", "knowledge_base_ids": [], "knowledge_ids": ["doc-a"]})
        self.assertCode("outside_scope", lambda: self.service.tool(self.selected(), "search", {"query": "权限", "kb_ids": ["kb-a"]}))

    def test_selected_document_cannot_read_sibling(self):
        self.assertCode("outside_scope", lambda: self.service.tool(self.selected(), "read", {"knowledge_id": "doc-b"}))
        self.assertFalse(any(path == "chunks/doc-b" for _, path, _ in self.transport.calls))

    def test_selected_document_cannot_search_other_document(self):
        self.assertCode("outside_scope", lambda: self.service.tool(self.selected(), "search", {"query": "a", "knowledge_ids": ["doc-b"]}))

    def test_missing_reference_fails_instead_of_searching_all(self):
        del self.transport.documents["doc-a"]
        self.assertCode("not_found", lambda: self.service.tool(self.selected(), "search", {"query": "a"}))
        self.assertFalse(any(path == "knowledge-search" for _, path, _ in self.transport.calls))

    def test_moved_document_invalidates_reference(self):
        self.transport.documents["doc-a"]["knowledge_base_id"] = "kb-b"
        self.assertCode("stale_reference", lambda: self.service.tool(self.selected(), "list", {}))

    def test_unselected_scope_includes_shared_catalog(self):
        result = self.service.tool(self.scope, "list", {})
        self.assertEqual(result["knowledge_base_ids"], ["kb-a", "kb-b"])

    def test_read_chunks_and_parse_failure(self):
        result = self.service.tool(self.selected(), "read", {"knowledge_id": "doc-a"})
        self.assertEqual(result["source"], self.ref)
        self.assertIn("只读用户", result["chunks"][0]["content"])
        self.transport.documents["doc-a"]["parse_status"] = "failed"
        self.assertCode("parse_failed", lambda: self.service.tool(self.selected(), "read", {"knowledge_id": "doc-a"}))

    def test_wiki_selection_does_not_authorize_whole_base_search(self):
        ref = self.service.reference(self.scope, "kb-a", "概览", wiki_slug="intro")
        scope = self.selected([ref])
        self.assertEqual(self.service.tool(scope, "read", {"kb_id": "kb-a", "wiki_slug": "intro"})["content"], "产品概览正文")
        self.assertCode("wiki_search_unavailable", lambda: self.service.tool(scope, "search", {"query": "a"}))

    def test_refresh_keeps_run_tenant_and_rotates_credentials(self):
        self.transport.expired = True
        self.service.request(self.scope, "GET", "/api/v1/auth/me")
        self.assertEqual(self.store.connection(secret=True)["credentials"]["token"], "new-token")
        self.assertEqual(self.transport.calls[-1][2]["headers"]["X-Tenant-ID"], "1")

    def test_space_switch_does_not_mutate_background_snapshot(self):
        self.service.switch_tenant("2")
        self.assertEqual(self.service.snapshot()["tenant_id"], "2")
        self.service.request(self.scope, "GET", "/api/v1/auth/me")
        self.assertEqual(self.transport.calls[-1][2]["headers"]["X-Tenant-ID"], "1")

    def test_account_relogin_revokes_old_run_even_for_same_user(self):
        self.service.login("http://localhost", "reader@example.test", "password")
        self.assertCode("identity_changed", lambda: self.service.tool(self.scope, "list", {}))

    def test_logout_revokes_local_state_when_remote_fails(self):
        self.transport.fail_status = 503
        with self.assertRaises(KnowledgeError):
            self.service.logout()
        self.assertIsNone(self.store.connection())
        self.assertCode("not_connected", lambda: self.service.tool(self.scope, "list", {}))

    def test_permission_error_is_not_retried(self):
        self.transport.fail_status = 403
        count = len(self.transport.calls)
        self.assertCode("forbidden", lambda: self.service.request(self.scope, "GET", "/api/v1/auth/me"))
        self.assertEqual(len(self.transport.calls), count + 1)

    def test_cancel_stops_before_request(self):
        count = len(self.transport.calls)
        self.assertCode("cancelled", lambda: self.service.request(self.scope, "GET", "/api/v1/auth/me", cancelled=lambda: True))
        self.assertEqual(len(self.transport.calls), count)

    def test_tool_cannot_override_identity(self):
        self.assertCode("invalid_arguments", lambda: self.service.tool(self.scope, "list", {"tenant_id": 2}))

    def test_normalization_preserves_independent_scope(self):
        context = {"knowledge_context": self.selected()}
        normalized = normalize_run_context(context)
        normalized["knowledge_context"]["refs"].clear()
        self.assertEqual(len(context["knowledge_context"]["refs"]), 1)
        self.assertNotIn("knowledge_context", normalize_run_context({}))

    def test_project_references_and_recent_are_not_knowledge_copies(self):
        self.store.references("project:x", [self.ref])
        loaded = self.store.references("project:x")
        loaded.clear()
        self.assertEqual(self.store.references("project:x"), [self.ref])
        self.store.visit(self.ref)
        self.assertEqual(self.store.recent(self.scope), [self.ref])
        self.assertEqual(self.store.recent({**self.scope, "tenant_id": "2"}), [])

    def test_upload_receipt_and_duplicate_protection(self):
        path = os.path.join(self.temp.name, "report.txt")
        with open(path, "w") as stream:
            stream.write("report")
        task = self.service.upload(self.scope, path, "kb-a")
        self.assertEqual(task["status"], "pending")
        self.assertEqual(self.service.check_upload(task)["status"], "completed")
        self.assertCode("duplicate_upload", lambda: self.service.upload(self.scope, path, "kb-a"))

    def test_unknown_upload_is_not_repeated(self):
        path = os.path.join(self.temp.name, "report.txt")
        with open(path, "w") as stream:
            stream.write("report")
        self.transport.fail_network = True
        count = len(self.transport.calls)
        self.assertCode("outcome_unknown", lambda: self.service.upload(self.scope, path, "kb-a"))
        self.assertEqual(len(self.transport.calls), count + 1)
        self.assertEqual(self.store.uploads()[0]["status"], "unknown")
        self.assertCode("duplicate_upload", lambda: self.service.upload(self.scope, path, "kb-a"))

    def test_host_bridge_does_not_accept_identity_from_json(self):
        with patch("core.knowledge_library.KnowledgeService", return_value=self.service):
            result = knowledge_script_call({"run_context": {"knowledge_context": self.scope}}, "list", '{"token":"x"}')
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "invalid_arguments")


if __name__ == "__main__":
    unittest.main()
