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

    def test_visualization_state_is_bound_to_conversation_and_file_hash(self):
        import hashlib
        from core.chat_storage import ChatStorage
        c = self.client
        cid = c.call("conversation.create")["id"]
        other = c.call("conversation.create")["id"]
        source = Path(self.temp.name) / "chart.html"
        source.write_text('<div id="chart">交互图表</div>', encoding="utf-8")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        storage = ChatStorage(str(Path(self.temp.name) / "chat_history" / "chat_history.sqlite"))
        storage.register_inline_visualization(cid, {"file":"chart.html", "path":str(source), "sha256":digest, "title":"图表", "origins":[]})
        skill = Path(self.temp.name) / "skills" / "visualize" / "SKILL.md"
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

    def test_theme_candidate_is_isolated_and_stale_apply_is_rejected(self):
        from core.theme_service import ThemeRepository
        from core.theme_tokens import default_design_tokens
        repository = ThemeRepository(self.temp.name)
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
