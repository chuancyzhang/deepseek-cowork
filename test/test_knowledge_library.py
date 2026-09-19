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
        if path == "auth/switch-tenant":
            return Response({"success": True, "token": "workspace-token", "refresh_token": "workspace-refresh",
                             "user": {"id": "user-a"}, "active_tenant": {"id": kwargs["json"]["tenant_id"]}})
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
        if path.endswith("/preview"):
            response = Response({})
            response.content = b"<!doctype html><h1>Preview while indexing</h1>"
            return response
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
        for bad in ("https://user:password@team.example", "file:///a", "http://team.example:bad", "http://[broken"):
            with self.assertRaises(KnowledgeError):
                service_url(bad)

    def test_browser_links_resolve_to_service_origin(self):
        self.assertEqual(service_url("http://localhost/platform/knowledge-bases/abc"), "http://localhost")
        self.assertEqual(service_url(" https://team.example:8443/platform/knowledge-bases/abc?q=word#files "),
                         "https://team.example:8443")
        self.assertEqual(service_url("http://[::1]:8080/#/platform"), "http://[::1]:8080")

    def test_html_preview_does_not_require_completed_index(self):
        self.transport.documents["doc-a"]["parse_status"] = "finalizing"
        self.assertIn(b"Preview while indexing", self.service.preview_html(self.scope, "doc-a"))
        self.transport.fail_status = 403
        self.assertCode("forbidden", lambda: self.service.preview_html(self.scope, "doc-a"))

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
        self.assertEqual(self.scope["tenant_id"], "1")
        self.assertCode("workspace_changed", lambda: self.service.request(self.scope, "GET", "/api/v1/auth/me"))
        switch = next(c for c in self.transport.calls if c[1] == "auth/switch-tenant")
        self.assertEqual(switch[2]["json"]["tenant_id"], 2)
        self.assertEqual(self.store.connection(secret=True)["credentials"]["token"], "workspace-token")

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


class CloudMcpFixture:
    """Recorded contract shapes from bundled Tencent references and Lexiang setup/base.

    This verifies adapter behavior, not availability of a live tenant's MCP schema.
    """
    def __init__(self):
        self.calls, self.fail, self.move_count = [], None, 0

    def discover(self, config):
        fields = {
            "manage.folder_list": {"folder_id": "string", "start": "integer"},
            "query_space_list": {"num": "integer"},
            "query_space_node": {"space_id": "string", "parent_id": "string", "num": "integer"},
            "manage.search_file": {"search_key": "string"}, "get_content": {"file_id": "string"},
            "manage.query_file_info": {"file_id": "string"},
            "manage.pre_import": {"file_name": "string", "file_size": "integer", "file_md5": "string"},
            "manage.async_import": {"file_name": "string", "file_size": "integer", "file_md5": "string", "task_id": "string", "file_key": "string"},
            "manage.import_progress": {"task_id": "string"},
            "manage.move_file_to_space": {"file_id": "string", "space_id": "string", "target_parent_id": "string"},
            "manage.move_file": {"file_id": "string", "target_folder_id": "string"},
            "whoami": {}, "space_list_spaces": {"page": "integer"}, "space_describe_space": {"space_id": "string"},
            "entry_list_children": {"parent_id": "string", "page": "integer"},
            "entry_describe_entry": {"entry_id": "string"}, "entry_describe_ai_parse_content": {"entry_id": "string"},
            "lexiang_search": {"query": "string", "type": "string"},
            "file_apply_upload": {"parent_entry_id": "string", "name": "string", "mime_type": "string", "size": "integer", "upload_type": "string"},
            "file_commit_upload": {"session_id": "string"},
        }
        return {"ok": True, "tools": [{"name": name, "input_schema": {"type": "object", "properties": {
            key: {"type": kind} for key, kind in props.items()}}} for name, props in fields.items()]}

    def call(self, config, tool, args):
        self.calls.append((tool, copy.deepcopy(args)))
        if self.fail == tool or self.fail == "401":
            return {"status": "error", "error": "401 secret-token" if self.fail == "401" else "transport failed"}
        data = {
            "whoami": {"company": {"code": "acme", "name": "示例企业", "company_domain": "https://lexiangla.com"}, "user": {"id": "reader", "name": "测试用户"}},
            "query_space_list": {"spaces": [{"space_id": "space", "title": "团队资料"}], "has_next": False},
            "query_space_node": {"children": [{"node_id": "doc", "title": "文档", "url": "https://docs.qq.com/doc/doc"}], "has_next": False},
            "manage.folder_list": {"list": [{"id": "doc", "title": "文档", "url": "https://docs.qq.com/doc/doc"}], "finish": True},
            "manage.search_file": {"list": [{"file_id": "doc", "title": "文档", "url": "https://docs.qq.com/doc/doc"}]},
            "manage.query_file_info": {"space_id": "space"},
            "get_content": {"content": "正文" * 8000},
            "space_list_spaces": {"spaces": [{"id": "space", "name": "团队资料", "team_id": "team", "team_name": "研发"}]},
            "space_describe_space": {"space": {"root_entry_id": "root"}},
            "entry_list_children": {"entries": [{"id": "doc", "name": "文档", "type": "page"}]},
            "entry_describe_entry": {"entry": {"id": args.get("entry_id"), "space_id": "space"}},
            "entry_describe_ai_parse_content": {"content": "乐享正文"},
            "lexiang_search": {"items": [{"id": "doc", "name": "文档", "space_id": "space"}]},
            "manage.pre_import": {"upload_url": "https://files.example.test/upload?secret=signed", "file_key": "key", "task_id": "task"},
            "manage.async_import": {"task_id": "task"},
            "manage.import_progress": {"progress": 100, "file_id": "doc", "file_url": "https://docs.qq.com/doc/doc"},
            "file_apply_upload": {"session_id": "upload-session", "upload_url": "https://files.example.test/upload?secret=signed"},
            "file_commit_upload": {"entry_id": "doc"},
        }.get(tool, {})
        if tool.startswith("manage.move_file"):
            self.move_count += 1
        return {"status": "ok", "structured_content": data}

    def get(self, url, **kwargs):
        return Response({"data": {"token": "secret-token"}})

    def put(self, url, **kwargs):
        self.calls.append(("put", {"size": len(kwargs["data"].read())}))
        return Response({}, 200)


class MultiSourceKnowledgeTests(unittest.TestCase):
    def setUp(self):
        from core.knowledge_sources import TencentDocsProvider, LexiangProvider, MultiSourceKnowledgeService
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = KnowledgeStore(self.temp.name, TestProtector())
        self.remote = CloudMcpFixture()
        self.tencent = TencentDocsProvider(self.store, self.remote, self.remote.call, self.remote.discover)
        self.lexiang = LexiangProvider(self.store, self.remote, self.remote.call, self.remote.discover)
        self.weknora = KnowledgeService(self.store, WeKnoraFixture())
        self.facade = MultiSourceKnowledgeService(self.store, {"weknora": self.weknora, "tencent-docs": self.tencent, "lexiang": self.lexiang})
        self.tencent.start_authorization()
        self.tencent.finish_authorization(confirmed=True)
        self.lexiang.connect("acme", "secret-token")

    def assertCode(self, code, action):
        with self.assertRaises(KnowledgeError) as result:
            action()
        self.assertEqual(result.exception.code, code)

    def test_cloud_cache_reuses_reads_and_expires_without_caching_identity_checks(self):
        from unittest.mock import patch
        for provider, tool, arguments, ttl in (
                (self.tencent, "query_space_node", {"space_id": "space", "parent_id": "", "num": 0}, 600),
                (self.tencent, "manage.search_file", {"search_key": "资料"}, 600),
                (self.tencent, "get_content", {"file_id": "doc"}, 1200),
                (self.lexiang, "entry_list_children", {"parent_id": "root", "page": 1}, 600),
                (self.lexiang, "lexiang_search", {"query": "资料", "type": "doc"}, 600),
                (self.lexiang, "entry_describe_ai_parse_content", {"entry_id": "doc"}, 1200)):
            scope = provider.snapshot()
            with patch("core.knowledge_sources.time.monotonic", return_value=100):
                original = provider.call(scope, tool, arguments)
                count = len(self.remote.calls)
                original["changed"] = True
                self.assertNotIn("changed", provider.call(scope, tool, arguments))
                self.assertEqual(len(self.remote.calls), count)
            with patch("core.knowledge_sources.time.monotonic", return_value=100 + ttl - 1):
                provider.call(scope, tool, arguments)
                self.assertEqual(len(self.remote.calls), count)
            with patch("core.knowledge_sources.time.monotonic", return_value=100 + ttl):
                provider.call(scope, tool, arguments)
                self.assertEqual(len(self.remote.calls), count + 1)
        count = len(self.remote.calls)
        self.lexiang.call(self.lexiang.snapshot(), "whoami", {})
        self.lexiang.call(self.lexiang.snapshot(), "whoami", {})
        self.assertEqual(len(self.remote.calls), count + 2)

    def test_cloud_cache_refresh_failure_and_late_read_invalidation(self):
        scope = self.tencent.snapshot()
        self.tencent.catalog(scope)
        self.remote.fail = "401"
        self.assertCode("unauthenticated", self.tencent.verify)
        self.assertFalse(self.tencent._cache)
        self.remote.fail = None
        caller = self.tencent.caller
        def invalidate_during_read(config, tool, arguments):
            result = caller(config, tool, arguments)
            self.tencent.clear_cache()
            return result
        self.tencent.caller = invalidate_during_read
        self.tencent.catalog(scope)
        self.assertFalse(self.tencent._cache)
        self.tencent.caller = caller
        self.tencent.catalog(scope)
        self.remote.fail = "manage.move_file"
        self.assertCode("remote_error", lambda: self.tencent.call(scope, "manage.move_file", {"file_id": "doc", "target_folder_id": "folder"}))
        self.assertFalse(self.tencent._cache)

    def test_cloud_cache_does_not_cross_accounts_sources_or_scope(self):
        scope = self.tencent.snapshot()
        self.tencent.call(scope, "get_content", {"file_id": "doc"})
        self.lexiang.read(self.lexiang.snapshot(), "doc", "space")
        self.assertEqual(self.lexiang.read(self.lexiang.snapshot(), "doc", "space")["content"], "乐享正文")
        ref = self.tencent.reference(scope, "space", "文档", "allowed")
        self.assertCode("outside_scope", lambda: self.tencent.tool({**scope, "refs": [ref]}, "read", {"item_id": "doc", "collection_id": "space"}))
        self.tencent.logout()
        self.assertFalse(self.tencent._cache)
        self.tencent.start_authorization()
        self.tencent.finish_authorization(confirmed=True)
        self.assertCode("identity_changed", lambda: self.tencent.call(scope, "get_content", {"file_id": "doc"}))
        count = len(self.remote.calls)
        self.tencent.call(self.tencent.snapshot(), "get_content", {"file_id": "doc"})
        self.assertEqual(len(self.remote.calls), count + 1)

    def test_two_stage_auth_never_fetches_before_explicit_confirmation(self):
        from unittest.mock import Mock
        self.tencent.transport = Mock()
        url = self.tencent.start_authorization()
        self.assertIn("docs.qq.com/scenario/open-claw.html?", url)
        self.tencent.transport.get.assert_not_called()
        self.assertCode("confirmation_required", self.tencent.finish_authorization)
        self.tencent.transport.get.assert_not_called()
        self.tencent._pending_auth = ("expired", 0)
        self.assertCode("auth_expired", lambda: self.tencent.finish_authorization(confirmed=True))
        self.tencent.transport.get.assert_not_called()

    def test_bad_tenant_and_401_do_not_replace_or_disclose_connection(self):
        before = self.lexiang.snapshot()
        self.assertCode("tenant_mismatch", lambda: self.lexiang.connect("other", "secret-token"))
        self.assertEqual(before, self.lexiang.snapshot())
        self.remote.fail = "401"
        count = len(self.remote.calls)
        with self.assertRaises(KnowledgeError) as error:
            self.lexiang.verify()
        self.assertEqual(error.exception.status, 401)
        self.assertNotIn("secret-token", str(error.exception))
        self.assertEqual(len(self.remote.calls), count + 1)

    def test_credentials_are_encrypted_and_identity_is_not_in_model_context(self):
        from core.knowledge_sources import context_message
        self.facade.select_source("lexiang")
        scope = self.facade.snapshot()
        message = json.dumps(context_message(scope, "request"))
        self.assertNotIn("secret-token", message)
        self.assertNotIn(scope["sources"]["lexiang"]["connection_id"], message)
        with open(self.store.path, "rb") as stream:
            self.assertNotIn(b"secret-token", stream.read())

    def test_mixed_same_id_references_require_source_and_cannot_expand(self):
        refs = [p.reference(p.snapshot(), "space", "同名", "doc") for p in (self.tencent, self.lexiang)]
        scope = self.facade.snapshot(refs, "conversation")
        self.assertCode("source_required", lambda: self.facade.tool(scope, "read", {"item_id": "doc"}))
        self.assertCode("outside_scope", lambda: self.facade.tool(scope, "read", {"source": "weknora", "item_id": "doc"}))
        result = self.facade.tool(scope, "read", {"source": "lexiang", "item_id": "doc"})
        self.assertEqual(result["content"], "乐享正文")
        count = len(self.remote.calls)
        self.assertCode("scoped_search_unsupported", lambda: self.facade.tool(scope, "search", {"source": "tencent-docs", "query": "x"}))
        self.assertEqual(count, len(self.remote.calls))
        self.assertCode("outside_scope", lambda: self.facade.tool(scope, "read", {"source": "tencent-docs", "item_id": "other"}))

    def test_collection_reference_can_browse_but_document_reference_cannot(self):
        ref = self.tencent.reference(self.tencent.snapshot(), "space", "整个空间")
        scope = self.facade.snapshot([ref])
        result = self.facade.tool(scope, "list", {})
        self.assertEqual(result["directories"][0]["entries"][0]["item_id"], "doc")
        self.assertCode("outside_scope", lambda: self.facade.tool(scope, "list", {"collection_id": "other"}))
        ref = self.tencent.reference(self.tencent.snapshot(), "space", "文档", "doc")
        scope = self.facade.snapshot([ref])
        self.assertCode("outside_scope", lambda: self.facade.tool(scope, "list", {"collection_id": "space"}))

    def test_lexiang_partial_last_page_does_not_loop(self):
        original = self.remote.call
        def paged(config, tool, args):
            if tool == "space_list_spaces":
                return {"status": "ok", "structured_content": {"spaces": [
                    {"id": str(i), "name": "资料库"} for i in (range(3) if args["page"] == 1 else range(3, 4))], "total": 4}}
            return original(config, tool, args)
        self.lexiang.caller = paged
        catalog = self.lexiang.catalog(self.lexiang.snapshot())
        self.assertEqual(len(catalog["shared"]), 4)

    def test_switching_page_preserves_submitted_scope_and_auth_change_invalidates_only_its_source(self):
        refs = [p.reference(p.snapshot(), "space", "文档", "doc") for p in (self.tencent, self.lexiang)]
        scope = self.facade.snapshot(refs)
        original = copy.deepcopy(scope)
        self.facade.select_source("weknora")
        self.assertEqual(scope, original)
        self.lexiang.connect("acme", "new-token")
        self.assertCode("identity_changed", lambda: self.facade.tool(scope, "read", {"source": "lexiang", "item_id": "doc"}))
        self.assertTrue(self.facade.tool(scope, "read", {"source": "tencent-docs", "item_id": "doc"})["has_more"])

    def test_legacy_import_is_idempotent_and_logout_cannot_resurrect(self):
        self.weknora.login("http://localhost", "user@test", "password")
        original = self.store.connection()
        with self.store.connect(write=True) as db:
            row = db.execute("SELECT public,secret FROM source_connections WHERE source='weknora'").fetchone()
            db.execute("INSERT INTO connection VALUES(1,?,?)", tuple(row))
            db.execute("DELETE FROM source_connections WHERE source='weknora'")
        migrated = KnowledgeStore(self.temp.name, TestProtector())
        self.assertEqual(migrated.connection(), original)
        self.weknora.logout()
        self.assertIsNone(KnowledgeStore(self.temp.name, TestProtector()).connection())
        self.assertIsNotNone(migrated.connection(source="lexiang"))

    def test_catalog_directory_content_and_legacy_weknora_parameters(self):
        values, more = self.tencent.children(self.tencent.snapshot(), "space")
        self.assertEqual(values[0]["id"], "doc")
        self.assertFalse(more)
        catalog = self.lexiang.catalog(self.lexiang.snapshot())
        self.assertEqual(catalog["organizations"][0]["name"], "研发")
        result = self.lexiang.search(self.lexiang.snapshot(), "内容")
        self.assertIn("company_from=acme", result[0]["url"])
        self.weknora.login("http://localhost", "user@test", "password")
        scope = self.facade.snapshot(requested_source="weknora")
        self.assertTrue(self.facade.tool(scope, "search", {"query": "权限"})["results"])

    def test_schema_mismatch_never_dispatches_a_guessed_call(self):
        connection = self.tencent.identity(self.tencent.snapshot())
        self.tencent.schemas(connection)["manage.search_file"]["properties"] = {"different_parameter": {"type": "string"}}
        count = len(self.remote.calls)
        self.assertCode("schema_mismatch", lambda: self.tencent.search(self.tencent.snapshot(), "x"))
        self.assertEqual(count, len(self.remote.calls))

    def test_tencent_pages_use_server_offsets_and_space_children(self):
        from core.knowledge_sources import PERSONAL
        original = self.remote.call
        def paged(config, tool, args):
            if tool == "manage.folder_list":
                self.remote.calls.append((tool, args))
                return {"status": "ok", "structured_content": {"list": [
                    {"id": "one", "title": "第一页"}, {"id": "two", "title": "第一页二"}]
                    if args["start"] == 0 else [{"id": "three", "title": "第二页"}], "finish": args["start"] != 0}}
            return original(config, tool, args)
        self.tencent.caller = paged
        rows, more = self.tencent.children(self.tencent.snapshot(), PERSONAL, page=2)
        self.assertEqual(rows[0]["id"], "three")
        self.assertFalse(more)
        self.assertEqual(self.remote.calls[-1][1]["start"], 2)

    def test_cancelled_cloud_read_never_dispatches_remote_call(self):
        count = len(self.remote.calls)
        self.assertCode("cancelled", lambda: self.tencent.tool(self.tencent.snapshot(), "read", {"item_id": "doc"}, lambda: True))
        self.assertEqual(count, len(self.remote.calls))

    def test_mcp_discovery_reads_all_pages_and_redacts_native_errors(self):
        import asyncio
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from core.mcp_client import _list_mcp_server_tools_async, describe_mcp_operation_error
        tool = lambda name: SimpleNamespace(name=name, description="", inputSchema={"type": "object"})
        session = SimpleNamespace(list_tools=AsyncMock(side_effect=[
            SimpleNamespace(tools=[tool("first")], nextCursor="next"),
            SimpleNamespace(tools=[tool("second")], nextCursor=None)]))
        @asynccontextmanager
        async def opened(_config):
            yield session, 5
        with patch("core.mcp_client._open_mcp_session", opened):
            result = asyncio.run(_list_mcp_server_tools_async({}))
        self.assertEqual([t["name"] for t in result], ["first", "second"])
        session.list_tools.assert_any_await(cursor="next")
        error = describe_mcp_operation_error({"redact_errors": True}, RuntimeError("401 Authorization=secret-token"))
        self.assertIn("401", error)
        self.assertNotIn("secret-token", error)

    def upload(self, provider):
        path = os.path.join(self.temp.name, "report.md")
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("report")
        return provider.upload(provider.snapshot(), path, "space", "folder")

    def test_tencent_upload_failed_placement_retains_file_and_resumes_without_import(self):
        task = self.upload(self.tencent)
        self.assertEqual(task["status"], "processing")
        self.remote.fail = "manage.move_file_to_space"
        self.tencent.check_upload(task)
        self.assertEqual(task["status"], "placement_failed")
        self.assertEqual(task["knowledge_id"], "doc")
        count = len(self.remote.calls)
        self.tencent.check_upload(task)
        self.assertEqual(count, len(self.remote.calls))
        self.remote.fail = None
        self.tencent.check_upload(task, resume=True)
        self.assertEqual(task["status"], "saved")
        self.assertEqual(sum(name == "manage.async_import" for name, _ in self.remote.calls), 1)
        self.assertCode("duplicate_upload", lambda: self.upload(self.tencent))

    def test_tencent_completed_personal_import_invalidates_directory_cache(self):
        from core.knowledge_sources import PERSONAL
        path = os.path.join(self.temp.name, "personal.md")
        with open(path, "w", encoding="utf-8") as stream:
            stream.write("成果")
        scope = self.tencent.snapshot()
        task = self.tencent.upload(scope, path, PERSONAL)
        self.tencent.children(scope, PERSONAL)
        self.assertTrue(self.tencent._cache)
        self.tencent.check_upload(task)
        self.assertEqual(task["status"], "saved")
        self.assertEqual(self.remote.move_count, 0)
        self.assertFalse(self.tencent._cache)

    def test_lexiang_commit_unknown_never_replayed(self):
        self.remote.fail = "file_commit_upload"
        self.assertCode("outcome_unknown", lambda: self.upload(self.lexiang))
        task = self.store.uploads()[0]
        self.assertEqual(task["stage"], "commit_upload")
        count = len(self.remote.calls)
        self.lexiang.check_upload(task)
        self.assertEqual(count, len(self.remote.calls))
        self.assertEqual(task["status"], "unknown")
        self.assertCode("duplicate_upload", lambda: self.upload(self.lexiang))

    def test_lexiang_upload_success_is_saved_not_falsely_searchable(self):
        task = self.upload(self.lexiang)
        self.assertEqual(task["status"], "saved")
        self.assertEqual(self.store.setting("last_upload_target")["source"], "lexiang")
        self.assertNotIn("signed", json.dumps(self.store.uploads()))

    def test_concurrent_upload_checks_place_once(self):
        from concurrent.futures import ThreadPoolExecutor
        task = self.upload(self.tencent)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(self.tencent.check_upload, copy.deepcopy(task)) for _ in range(2)]
            for job in jobs:
                job.result()
        self.assertEqual(self.remote.move_count, 1)
        self.assertEqual(self.store.uploads()[0]["status"], "saved")


if __name__ == "__main__":
    unittest.main()
