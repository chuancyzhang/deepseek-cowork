"""Domain parity and reliability through the real Qt-blocked desktop process."""
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from test_desktop_service import ServiceClient, QA_ROOT


class ManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(dir=QA_ROOT)
        cls.client = ServiceClient(cls.temp.name)
        cls.data = Path(cls.client.call("settings.get")["data_dir"])

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.temp.cleanup()

    def test_theme_preview_does_not_change_active_theme(self):
        c = self.client
        before = c.call("themes.list")
        preview = c.call("themes.resolve", id="builtin_paper", dark=True)
        self.assertEqual(preview["id"], "builtin_paper")
        self.assertEqual(c.call("themes.list")["active"], before["active"])
        c.call("themes.appearance", value={"mode": "system", "reading": {"font_scale": 1.2, "density": "compact"}})
        self.assertEqual(c.call("settings.get")["theme"], "system")
        c.call("themes.activate", id="builtin_mist")
        self.assertEqual(c.call("themes.resolve")["id"], "builtin_mist")

    def test_message_files_restore_existing_references_without_mutating_history(self):
        from core.chat_storage import ChatStorage
        c = self.client
        conversation = c.call("conversation.create")
        cid = conversation["id"]
        workspace = Path(conversation["meta"]["workspace_dir"])
        folder = workspace / "公众号文章 AI安全"
        folder.mkdir()
        document = folder / "公众号文章.docx"
        document.write_bytes(b"fixture")
        outside = self.data / "other.txt"
        outside.write_text("another conversation", encoding="utf-8")
        storage = ChatStorage(str(self.data / "chat_history" / "chat_history.sqlite"))
        storage.replace_messages(cid, [
            {"id":"user", "role":"user", "content":"整理文章"},
            {"id":"answer", "role":"assistant", "content":f"[文件](<{document.as_uri()}>)\n\n`{document}`\n\n[不存在](missing.docx)\n\n{outside}"},
        ])
        before = storage.get_messages(cid)
        value = c.call("conversation.get", conversation_id=cid)
        files = value["render_messages"][1]["file_references"]
        self.assertEqual(len(files), 1)
        self.assertEqual(Path(files[0]["path"]), document)
        self.assertEqual(files[0]["name"], "公众号文章 AI安全/公众号文章.docx")
        self.assertEqual(files[0]["label"], "DOCX")
        self.assertIn(str(document), files[0]["references"])
        self.assertEqual(value["messages"], before)
        self.assertEqual(storage.get_messages(cid), before)
        document.unlink()
        self.assertEqual(c.call("conversation.get", conversation_id=cid)["render_messages"][1]["file_references"], [])

    def test_visualization_state_is_bound_to_conversation_and_file_hash(self):
        import hashlib
        from core.chat_storage import ChatStorage
        c = self.client
        cid = c.call("conversation.create")["id"]
        other = c.call("conversation.create")["id"]
        source = Path(self.temp.name) / "chart.html"
        source.write_text('<div id="chart">交互图表</div>', encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        storage = ChatStorage(str(self.data / "chat_history" / "chat_history.sqlite"))
        storage.register_inline_visualization(cid, {"file":"chart.html", "path":str(source), "sha256":digest, "title":"图表", "origins":[]})
        skill = self.data / "skills" / "visualize" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: visualize\ndescription: Local visualization fixture\n---\nRender the published visualization.\n", encoding="utf-8")
        c.call("skills.enable", name="visualize", enabled=True)
        loaded = c.call("visualization.get", conversation_id=cid, file="chart.html")
        from html import unescape
        self.assertIn("connect-src 'none'", unescape(loaded["html"]))
        self.assertNotIn("QWebChannel", loaded["html"])
        self.assertFalse(loaded["read_only"])
        with self.assertRaisesRegex(RuntimeError, "缺少会话上下文"):
            c.call("visualization.get", file="chart.html")
        c.call("visualization.state", conversation_id=cid, file="chart.html", sha256=digest, state={"selected":3})
        self.assertEqual(storage.get_inline_visualization_state(cid, "chart.html", digest), {"selected":3})
        with self.assertRaisesRegex(RuntimeError, "不属于"):
            c.call("visualization.get", conversation_id=other, file="chart.html")
        with self.assertRaisesRegex(RuntimeError, "版本"):
            c.call("visualization.state", conversation_id=cid, file="chart.html", sha256="outdated", state={"selected":4})
        c.call("skills.enable", name="visualize", enabled=False)
        try:
            self.assertTrue(c.call("visualization.get", conversation_id=cid, file="chart.html")["read_only"])
            with self.assertRaisesRegex(RuntimeError, "只读"):
                c.call("visualization.state", conversation_id=cid, file="chart.html", sha256=digest, state={"selected":4})
        finally:
            c.call("skills.enable", name="visualize", enabled=True)
        source.write_text('<div id="chart">外部修改</div>', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "已变化"):
            c.call("visualization.get", conversation_id=cid, file="chart.html")
        self.assertEqual(storage.get_inline_visualization_state(cid, "chart.html", digest), {"selected":3})

    def test_skills_experts_and_mcp_inventory(self):
        c = self.client
        skills = c.call("skills.inventory")
        self.assertTrue(skills)
        detail = c.call("skills.get", name=skills[0]["name"])
        self.assertIn("files", detail)
        c.call("experts.save", value={"name": "测试专家", "system_prompt": "核对用户提供的资料", "skill_names": []})
        expert = next(item for item in c.call("experts.list") if item["name"] == "测试专家")
        c.call("experts.delete", id=expert["id"])
        c.call("mcp.save", value={"name": "本机测试连接", "command": "unused-fixture-command", "enabled": False})
        server = next(item for item in c.call("mcp.list") if item["name"] == "本机测试连接")
        self.assertFalse(server["enabled"])
        c.call("mcp.delete", id=server["id"])

    def test_builtin_html_expert_is_listed_without_persisting_or_overwriting_it(self):
        c = self.client
        config_file = self.data / "config.json"
        stored = json.loads(config_file.read_text(encoding="utf-8"))["agent_profiles"]
        builtin = next(item for item in c.call("experts.list") if item["id"] == "builtin-html-creator")
        self.assertTrue(builtin["builtin"])
        self.assertTrue(builtin["enabled"])
        self.assertEqual(json.loads(config_file.read_text(encoding="utf-8"))["agent_profiles"], stored)
        with self.assertRaisesRegex(RuntimeError, "内置专家不能修改或删除"):
            c.call("experts.save", value={**builtin, "system_prompt": "changed"})
        with self.assertRaisesRegex(RuntimeError, "内置专家不能修改或删除"):
            c.call("experts.delete", id=builtin["id"])
        copy_id = uuid.uuid4().hex
        try:
            c.call("experts.save", value={**builtin, "id": copy_id, "name": "HTML创作"})
            profiles = c.call("experts.list")
            original = next(item for item in profiles if item["id"] == builtin["id"])
            copied = next(item for item in profiles if item["id"] == copy_id)
            self.assertEqual(original["name"], "HTML创作（内置）")
            self.assertEqual(original["system_prompt"], builtin["system_prompt"])
            self.assertFalse(copied.get("builtin"))
            persisted = json.loads(config_file.read_text(encoding="utf-8"))["agent_profiles"]
            self.assertNotIn(builtin["id"], [item["id"] for item in persisted])
        finally:
            c.call("experts.delete", id=copy_id)
        self.assertEqual(c.call("experts.list")[-1], builtin)

    def test_mcp_json_parse_normalizes_imports_without_saving_or_connecting(self):
        c = self.client
        before = c.call("mcp.list")
        parsed = c.call("mcp.parse", value='\ufeff' + json.dumps({"mcpServers": {
            "local": {"command": "npx", "args": ["-y", "fixture-package", "C:/sample folder"], "env": {"TOKEN": "fixture-token"}, "enabled": False},
            "remote": {"url": "https://example.invalid/mcp", "headers": {"Authorization": "fixture-header"}},
        }}))
        self.assertEqual([server["name"] for server in parsed], ["local", "remote"])
        self.assertEqual([server["transport"] for server in parsed], ["stdio", "streamable_http"])
        self.assertEqual(parsed[0]["args"][-1], "C:/sample folder")
        self.assertEqual(parsed[0]["env"], {"TOKEN": "fixture-token"})
        self.assertFalse(parsed[0]["enabled"])
        self.assertEqual(parsed[1]["headers"], {"Authorization": "fixture-header"})
        for value in ({"name": "single", "url": "https://example.invalid/mcp"},
                      {"mcp_servers": [{"name": "array", "command": "fixture-command"}]},
                      [{"name": "list", "command": "fixture-command"}]):
            self.assertEqual(len(c.call("mcp.parse", value=value)), 1)
        for value in ('{"mcpServers":', {"mcpServers": {}}, [None], {"name": "incomplete"}):
            with self.assertRaisesRegex(RuntimeError, "MCP JSON 解析失败"):
                c.call("mcp.parse", value=value)
        self.assertEqual(c.call("mcp.list"), before)
        with self.assertRaisesRegex(RuntimeError, "目前支持 stdio 和 Streamable HTTP"):
            c.call("mcp.parse", value={"type": "sse", "url": "https://example.invalid/sse"})

    def test_theme_candidate_is_isolated_and_stale_apply_is_rejected(self):
        from core.theme_service import ThemeRepository
        from core.theme_tokens import default_design_tokens
        repository = ThemeRepository(str(self.data))
        before = self.client.call("themes.list")["active"]
        candidate = repository.write_preview(name="候选验收", overrides={"font_scale": 1.1}, default_tokens=default_design_tokens())
        preview = self.client.call("themes.preview")
        self.assertEqual(preview["resolved"]["font_scale"], 1.1)
        self.assertEqual(self.client.call("themes.list")["active"], before)
        with self.assertRaisesRegex(RuntimeError, "已更新"):
            self.client.call("themes.commit_preview", id=candidate["preview_id"], revision=0, name="过期候选")
        self.assertEqual(self.client.call("themes.list")["active"], before)
        self.client.call("themes.discard_preview", id=candidate["preview_id"], revision=candidate["revision"])
        self.assertIsNone(self.client.call("themes.preview")["preview"])
        self.assertEqual(self.client.call("themes.list")["active"], before)

    def test_sensitive_variables_are_not_returned_and_rejection_is_durable(self):
        c = self.client
        c.call("variables.save", value={"name": "management_test_secret", "kind": "secret", "value": "secret-fixture-1234"})
        self.assertNotIn("secret-fixture-1234", json.dumps(c.call("variables.list")))
        cid = c.call("conversation.create")["id"]
        identity = uuid.uuid4().hex
        with self.assertRaisesRegex(RuntimeError, "API Key"):
            c.call("conversation.send", conversation_id=cid, request_id=identity, message_id=uuid.uuid4().hex, text="保留此输入", model_id="missing")
        receipt = c.call("conversation.receipt", conversation_id=cid, request_id=identity)
        self.assertTrue(receipt["rejected"])
        self.assertFalse(receipt["accepted"])
        self.assertEqual(c.call("conversation.get", conversation_id=cid)["messages"], [])

    def test_connection_status_does_not_require_gui_or_network(self):
        c = self.client
        self.assertIn("sources", c.call("library.status"))
        self.assertIsInstance(c.call("enterprise.status"), dict)
        self.assertEqual(c.call("jobs.list"), [])
        self.assertIn("chat_workspace_root", c.call("settings.general"))

    def test_settings_pages_preserve_conflicts_and_memory_scopes(self):
        c = self.client
        for name in ("preferences", "memory", "security", "storage", "experiments", "logs", "update", "im"):
            self.assertIn("sections", c.call("settings.page", page=name))
        page = c.call("settings.page", page="preferences")
        saved = c.call("settings.page_save", page="preferences", revision=page["revision"], value={"soul": "先给结论，再给证据。"})
        with self.assertRaisesRegex(RuntimeError, "已被其他操作修改"):
            c.call("settings.page_save", page="preferences", revision=page["revision"], value={"soul": "过期草稿"})
        c.call("settings.page_save", page="preferences", revision=saved["revision"], value={"soul": "第二次有效保存"})
        self.assertEqual(c.call("settings.page", page="preferences")["values"]["soul"].strip(), "第二次有效保存")
        memory = c.call("settings.page", page="memory")
        c.call("settings.page_save", page="memory", revision=memory["revision"], value={"summary": "全局事实"})
        with self.assertRaisesRegex(RuntimeError, "已有项目"):
            c.call("settings.page", page="memory", context="D:/unregistered-project")
        self.assertEqual(c.call("settings.page", page="memory")["values"]["summary"].strip(), "全局事实")

    def test_retired_controls_do_not_overwrite_settings_or_restore_speech(self):
        c = self.client
        c.call("settings.context", target_tokens=1000000)
        page = c.call("settings.page", page="experiments")
        self.assertNotIn("context_target", page["values"])
        c.call("settings.page_save", page="experiments", revision=page["revision"], value={**page["values"], "context_target": 0})
        self.assertEqual(c.call("settings.get")["context_policy"]["target_tokens"], 1000000)
        components = c.call("settings.page", page="components")
        self.assertNotIn("node_source", components["values"])
        items = [item["id"] for section in components["sections"] for item in section.get("items", [])]
        self.assertEqual(items, ["documents", "data-analysis"])
        for action in ("source.node", "node.install", "component.install:speech", "component.install:finance"):
            with self.assertRaisesRegex(RuntimeError, "不可用"):
                c.call("settings.action", page="components", action=action)
        self.assertNotIn("speech-to-text", [item["name"] for item in c.call("skills.inventory")])
        with self.assertRaisesRegex(RuntimeError, "已下线"):
            c.call("skills.enable", name="speech-to-text", enabled=True)

    def test_settings_reject_invalid_changes_without_partial_save(self):
        c = self.client
        before = c.call("settings.page", page="storage")
        values = {**before["values"], "default_workspace": self.temp.name, "chat_workspace_root": str(Path(self.temp.name) / "missing")}
        with self.assertRaisesRegex(RuntimeError, "目录不存在"):
            c.call("settings.page_save", page="storage", revision=before["revision"], value=values)
        self.assertEqual(c.call("settings.page", page="storage")["values"], before["values"])
        security = c.call("settings.page", page="security")
        with self.assertRaisesRegex(RuntimeError, "至少选择"):
            c.call("settings.page_save", page="security", revision=security["revision"], value={**security["values"], "enabled": True, "tokenize": True})
        self.assertEqual(c.call("settings.page", page="security")["values"], security["values"])
        with self.assertRaisesRegex(RuntimeError, "不可用"):
            c.call("settings.action", page="preferences", action="component.remove:documents")

    def test_preview_files_are_scoped_and_do_not_create_editable_sessions(self):
        from PIL import Image
        from pypdf import PdfWriter
        c = self.client
        conversation = c.call("conversation.create")
        root = Path(conversation["meta"]["workspace_dir"])
        image = root / "图片.png"
        Image.new("RGB", (20, 20), "white").save(image)
        pdf = root / "资料.pdf"
        writer = PdfWriter(); writer.add_blank_page(width=100, height=100); writer.write(pdf)
        for path, kind in ((image, "image"), (pdf, "pdf")):
            file = c.call("file.open", conversation_id=conversation["id"], path=str(path))
            self.assertEqual(file["payload"]["preview_kind"], kind)
            self.assertTrue(file["payload"]["read_only"])
            self.assertEqual(c.call("file.describe", conversation_id=conversation["id"], path=str(path))["fingerprint"], file["fingerprint"])
            with self.assertRaisesRegex(RuntimeError, "编辑会话已失效"):
                c.call("file.save", conversation_id=conversation["id"], id=file["id"], payload="wrong")
        other = c.call("conversation.create")
        with self.assertRaisesRegex(RuntimeError, "不属于"):
            c.call("file.open", conversation_id=other["id"], path=str(image))

    def test_office_payload_roundtrip_and_external_conflict(self):
        from openpyxl import Workbook, load_workbook
        c = self.client
        conversation = c.call("conversation.create")
        root = Path(conversation["meta"]["workspace_dir"])
        path = root / "预算.xlsx"
        workbook = Workbook()
        workbook.active["A1"] = "原始内容"
        workbook.save(path)
        session = c.call("file.open", conversation_id=conversation["id"], path=str(path))
        snapshot = session["payload"]["snapshot"]
        sheet = snapshot["sheets"][snapshot["sheetOrder"][0]]
        sheet["cellData"]["0"]["0"]["v"] = "已更新"
        payload = {"kind": "sheet", "value": json.dumps(snapshot)}
        c.call("file.save", conversation_id=conversation["id"], id=session["id"], payload=payload)
        self.assertEqual(load_workbook(path).active["A1"].value, "已更新")
        workbook.active["A1"] = "外部修改"
        workbook.save(path)
        with self.assertRaisesRegex(RuntimeError, "其他程序"):
            c.call("file.save", conversation_id=conversation["id"], id=session["id"], payload=payload)
        self.assertEqual(load_workbook(path).active["A1"].value, "外部修改")
        recovered = c.call("file.save_copy", conversation_id=conversation["id"], id=session["id"], name="预算-恢复.xlsx", payload=payload)
        self.assertEqual(load_workbook(recovered["path"]).active["A1"].value, "已更新")
        self.assertEqual(load_workbook(path).active["A1"].value, "外部修改")
        with self.assertRaises(RuntimeError):
            c.call("file.save_copy", conversation_id=conversation["id"], id=session["id"], name="预算-恢复.xlsx", payload=payload)


def test_config_save_failure_preserves_disk_and_memory(tmp_path, monkeypatch):
    from core.desktop_config import DesktopConfigManager
    monkeypatch.setenv("COWORK_APP_DATA_DIR", str(tmp_path))
    config = DesktopConfigManager(migrate_legacy=False)
    config.set("fixture", "original")
    before = Path(config.config_path).read_bytes()
    monkeypatch.setattr(config, "_write_config_or_raise", lambda: (_ for _ in ()).throw(OSError("disk fixture")))
    try:
        config.set("fixture", "changed")
        raise AssertionError("save failure must propagate")
    except OSError:
        pass
    assert config.get("fixture") == "original"
    assert Path(config.config_path).read_bytes() == before

def test_local_library_aggregates_projects_and_keeps_file_scope(tmp_path):
    import threading
    from types import SimpleNamespace
    from unittest.mock import Mock
    import pytest
    from core.local_library import LocalLibrary
    from core.desktop_jobs import JobCancelled
    first, second = tmp_path / 'one', tmp_path / 'two'
    first.mkdir(); second.mkdir()
    (first / 'notes.md').write_text('# first', encoding='utf-8')
    (second / 'notes.md').write_text('# second', encoding='utf-8')
    (first / 'node_modules').mkdir()
    (first / 'node_modules' / 'hidden.md').write_text('excluded')
    nested = first / 'folder'; nested.mkdir()
    (nested / 'detail.txt').write_text('detail')
    service = SimpleNamespace(config=SimpleNamespace(get_projects=lambda:[{'path':str(first),'name':'项目一'},{'path':str(second),'name':'项目二'}]),storage=SimpleNamespace(list_conversations_by_workspace=lambda:{}))
    library = LocalLibrary(service)
    progress, cancelled = Mock(), threading.Event()
    items = library.files({}, cancelled, progress)['items']
    assert {item['title'] for item in items} == {'notes.md','detail.txt'}
    assert len(items) == 3
    projects = library.projects()
    pid = projects[0]['id']
    result = library.files({'collection':pid},cancelled,progress)
    assert any(item['is_folder'] and item['title']=='folder' for item in result['items'])
    assert library.read({'collection':pid,'item':str(first/'notes.md')})['text']=='# first'
    assert library.files({'query':'detail'},cancelled,progress)['total']==1
    with pytest.raises(ValueError,match='不属于'):
        library.read({'collection':pid,'item':str(second/'notes.md')})
    (first/'notes.md').unlink()
    with pytest.raises(ValueError,match='暂时不可用'):
        library.read({'collection':pid,'item':str(first/'notes.md')})
    cancelled.set()
    with pytest.raises(JobCancelled):
        library.files({},cancelled,progress)
    assert (second/'notes.md').read_text()=='# second'

    # A project derived from chat history must not reappear after archival,
    # even when its directory is unavailable. Restoring it reuses its identity.
    service.config.get_projects = lambda: [{'path':str(first),'name':'项目一','archived':True}]
    service.storage.list_conversations_by_workspace = lambda: {str(first):[{'workspace_dir':str(first)}]}
    assert library.projects() == []
    with pytest.raises(ValueError, match='不在资料库'):
        library.checked(pid)
    service.config.get_projects = lambda: [{'path':str(first),'name':'项目一','archived':False}]
    assert library.projects()[0]['id'] == pid


def test_background_jobs_keep_searchable_logs_and_recover_interrupted_work(tmp_path):
    import threading
    import time
    from types import SimpleNamespace
    from unittest.mock import Mock
    from core.desktop_store import DesktopStore, DesktopChatStorage
    from core.desktop_jobs import DesktopJobs, JobCancelled, LOG_LIMIT
    storage = DesktopChatStorage(str(tmp_path / "jobs.sqlite"))
    service = SimpleNamespace(storage=storage, store=DesktopStore(storage), publish=Mock())
    jobs = DesktopJobs(service)
    # A cancel click racing the completion event must not resurrect a finished job.
    service.publish.side_effect = lambda event: jobs.cancel(event["data"]["id"]) if event["data"]["status"] == "completed" else None
    release = threading.Event()
    def wait(identity):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = jobs.get(identity)
            if result["status"] in {"completed", "failed", "cancelled"}:
                return result
            time.sleep(.01)
        raise AssertionError("job did not finish")
    try:
        def complete(cancelled, progress):
            progress("读取说明文件", qr_data="private-qr")
            progress("读取说明文件", qr_data="another-qr")
            return {"private_result": "should-not-be-logged"}
        identity = jobs.submit("文档预览", complete)["job_id"]
        result = wait(identity)
        assert result["status"] == "completed"
        assert [entry["message"] for entry in result["logs"]] == ["等待处理", "正在处理…", "读取说明文件", "已完成"]
        assert "private" not in json.dumps(result["logs"])
        assert jobs.list(query="说明文件")[0]["id"] == identity
        assert jobs.list(status="failed") == []
        entered = threading.Event()
        def cancellable(cancelled, progress):
            progress("等待取消")
            entered.set(); release.wait(5)
            if cancelled.is_set():
                progress("正在结束处理")
                assert jobs.get(cancel_id)["status"] == "stopping"
                raise JobCancelled("已取消，原文件保留。")
        cancel_id = jobs.submit("取消示例", cancellable)["job_id"]
        assert entered.wait(2)
        jobs.cancel(cancel_id); release.set()
        result = wait(cancel_id)
        assert result["status"] == "cancelled"
        assert "stopping" in [entry["status"] for entry in result["logs"]]
        def fail(cancelled, progress):
            progress("正在转换")
            raise ValueError("无法读取模板")
        failed_id = jobs.submit("转换失败", fail)["job_id"]
        assert wait(failed_id)["logs"][-1]["message"] == "无法读取模板"
        assert jobs.list(query="模板", status="failed")[0]["id"] == failed_id
        for index in range(110):
            service.store.put_state("job:old-%03d" % index, {"id":"old-%03d" % index,"title":"历史记录" if index else "100% 特殊记录","status":"completed","created_at":1})
        assert jobs.list(query="100%")[0]["id"] == "old-000"
        first = jobs.list(limit=50)
        second = jobs.list(before=first[-1]["id"],limit=50)
        assert len(first) == len(second) == 50
        assert not {item["id"] for item in first} & {item["id"] for item in second}
        service.store.put_state("job:unfinished", {"id":"unfinished","title":"未完成操作","status":"running","created_at":1,"logs":[{"at":1,"status":"running","message":"已经完成的步骤"}]})
        for index in range(105):
            identity = "0-unfinished-%03d" % index
            service.store.put_state("job:" + identity, {"id":identity,"title":"较早未完成操作","status":"queued","created_at":1})
        service.store.put_state("job:bounded", {"id":"bounded","status":"running","logs":[{"at":i,"status":"running","message":str(i)} for i in range(LOG_LIMIT)]})
        jobs.save({"id":"bounded","status":"completed","message":"已完成"})
        assert jobs.get("bounded")["logs_omitted"] == 1
        assert len(jobs.get("bounded")["logs"]) == LOG_LIMIT
    finally:
        release.set();jobs.close()
    recovered = DesktopJobs(service)
    try:
        result = recovered.get("unfinished")
        assert result["status"] == "interrupted"
        assert result["logs"][0]["message"] == "已经完成的步骤"
        assert result["logs"][-1]["status"] == "interrupted"
        assert recovered.list(status="active") == []
        assert recovered.get("0-unfinished-000")["status"] == "interrupted"
        assert "logs" not in recovered.get("old-000")
    finally:
        recovered.close()
