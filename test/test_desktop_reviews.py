"""Real persistence and file publication around the tool review boundary."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.desktop_reviews import DesktopReviews
from core.desktop_store import DesktopChatStorage, DesktopStore
from core.desktop_workbench import DesktopFiles, DesktopWorkbench
from core.skill_manager import SkillManager


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORK_APP_DATA_DIR", str(tmp_path / "data"))
    storage = DesktopChatStorage(str(tmp_path / "chat.db"))
    store = DesktopStore(storage)
    config = SimpleNamespace(data_dir=str(tmp_path / "data"), get_chat_workspace_root=lambda: str(tmp_path), get_selected_model_id=lambda: "fixture")
    files = DesktopFiles(storage, config)
    for cid in ("a", "b"):
        root = tmp_path / cid
        root.mkdir()
        storage.save_conversation(cid, [{"id": cid + "-u", "role": "user", "content": "核对资料并整理方法"},
                                       {"id": cid + "-answer", "role": "assistant", "content": "先核对来源，再形成结论。"}],
                                  meta={"workspace_dir": str(root), "workspace_source": "project"})
    manager = SkillManager(auto_load=False, load_mcp_tools=False)
    manager.skills_dirs = [str(tmp_path / "data" / "ai_skills")]
    manager.load_skills(load_mcp_tools=False)
    events = []
    s = SimpleNamespace(storage=storage, store=store, config=config, files=files, workspace=files.workspace,
                        checked_file=files.checked_file, publish=events.append, events=events,
                        state=SimpleNamespace(skill_catalog=SimpleNamespace(snapshot=lambda: SimpleNamespace(manager=manager))),
                        conversations=SimpleNamespace(idle=Mock()), public_run=lambda value: value,
                        management=SimpleNamespace(skill_change=Mock(side_effect=lambda *_: manager.load_skills(load_mcp_tools=False))))
    s.reviews = DesktopReviews(s)
    s.runs = {}
    s.store.get_run = lambda identity: copy.deepcopy(s.runs.get(identity))
    def dispatch(method, p):
        assert method == "conversation.send"
        s.runs[p["request_id"]] = {"id": p["request_id"], "conversation_id": p["conversation_id"], "status": "running", "error": ""}
        return {"accepted": True}
    s.dispatch = Mock(side_effect=dispatch)
    return s


def skill(s, **extra):
    return s.reviews.submit_tool("a", "run", {"kind": "skill", "content": {
        "skill_name": "evidence-check", "description": "核对资料: 保留依据", "instructions_md": "## 步骤\n逐条核对来源。\n\n## 验证\n保留反例与限制。", **extra}})


def test_tool_submission_is_durable_isolated_deduplicated_and_not_published(service):
    s = service
    receipt = skill(s)
    identity = receipt["review"]["id"]
    assert skill(s) == receipt
    assert len(s.reviews.entries("a")) == 1
    assert not (Path(s.config.data_dir) / "ai_skills" / "evidence-check").exists()
    restarted = DesktopReviews(s)
    assert restarted.get("a", identity)["content"]["instructions_md"].startswith("## 步骤")
    with pytest.raises(ValueError, match="不属于"):
        restarted.get("b", identity)
    ready = s.reviews.get("a", identity)
    with pytest.raises(ValueError, match="人工审阅"):
        s.reviews.submit_tool("a", "run", {"kind": "skill", "draft_id": identity, "content": {**ready["content"], "instructions_md": "覆盖编辑"}})
    with pytest.raises(ValueError, match="另一轮"):
        s.reviews.submit_tool("a", "other-run", {"kind": "skill", "draft_id": identity, "content": ready["content"]})


def test_skill_saving_filters_resources_and_returns_one_receipt(service):
    s = service
    identity = skill(s, resources=[{"path": "references/keep.md", "kind": "reference", "content": "核对依据"},
                                   {"path": "references/skip.md", "kind": "reference", "content": "不附带", "selected": False}])["review"]["id"]
    value = s.reviews.get("a", identity)
    p = {"conversation_id": "a", "id": identity, "revision": value["revision"]}
    saved = s.reviews.dispatch("reviews.save", p)
    assert saved["status"] == "saved"
    target = Path(saved["saved_path"])
    assert (target / "references/keep.md").is_file()
    assert not (target / "references/skip.md").exists()
    from core.skill_adapter import parse_skill_md_content
    meta, _body = parse_skill_md_content(str(target / "SKILL.md"))
    assert meta["description"] == "核对资料: 保留依据"
    assert s.reviews.dispatch("reviews.save", p)["saved_path"] == str(target)
    assert s.management.skill_change.call_count == 1


def test_target_changed_after_review_needs_explicit_rebase(service):
    s = service
    first = skill(s)["review"]["id"]
    initial = s.reviews.get("a", first)
    saved = s.reviews.dispatch("reviews.save", {"conversation_id": "a", "id": first, "revision": initial["revision"]})
    identity = s.reviews.submit_tool("a", "second", {"kind": "skill", "content": {"skill_name": "new-method", "description": "补充核对", "instructions_md": "检查遗漏和反例。"}})["review"]["id"]
    value = s.reviews.get("a", identity)
    selected = s.reviews.dispatch("reviews.update", {"conversation_id": "a", "id": identity, "revision": value["revision"], "value": {"target_skill": "evidence-check"}})
    file = Path(saved["saved_path"]) / "SKILL.md"
    file.write_text(file.read_text(encoding="utf-8") + "\n人工追加的新指导。\n", encoding="utf-8")
    with pytest.raises(ValueError, match="审阅期间已更新"):
        s.reviews.dispatch("reviews.save", {"conversation_id": "a", "id": identity, "revision": selected["revision"]})
    rebased = s.reviews.dispatch("reviews.rebase", {"conversation_id": "a", "id": identity, "revision": selected["revision"]})
    assert "人工追加的新指导" in rebased["target_snapshot"]["body"]
    result = s.reviews.dispatch("reviews.save", {"conversation_id": "a", "id": identity, "revision": rebased["revision"]})
    assert result["status"] == "saved"
    assert "人工追加的新指导" in file.read_text(encoding="utf-8")
    assert "检查遗漏和反例" in (file.parent / "experience/entries.jsonl").read_text(encoding="utf-8")


def test_office_snapshots_real_files_refuses_overwrite_and_keeps_source(service):
    from openpyxl import Workbook, load_workbook
    s = service
    original = s.workspace("a") / "input.xlsx"
    workbook = Workbook()
    workbook.active.append(["依据", "数量", "合计"])
    workbook.active.append(["访谈", 8, "=B2*2"])
    workbook.save(original)
    receipt = s.reviews.submit_tool("a", "office-run", {"kind": "office", "file_path": str(original), "title": "分析表"})
    identity = receipt["review"]["id"]
    value = s.reviews.get("a", identity)
    assert Path(value["file_path"]).read_bytes() == original.read_bytes()
    with pytest.raises(ValueError, match="不属于"):
        s.files.checked_file("b", value["file_path"])
    opened = s.reviews.dispatch("reviews.open", {"conversation_id": "a", "id": identity})
    assert opened["name"] == "分析表.xlsx"
    destination = s.workspace("a") / "分析表.xlsx"
    destination.write_bytes(b"user file")
    with pytest.raises(ValueError, match="同名文件"):
        s.reviews.dispatch("reviews.save", {"conversation_id": "a", "id": identity, "revision": value["revision"]})
    assert destination.read_bytes() == b"user file"
    changed = s.reviews.dispatch("reviews.update", {"conversation_id": "a", "id": identity, "revision": value["revision"], "value": {"title": "分析表副本"}})
    saved = s.reviews.dispatch("reviews.save", {"conversation_id": "a", "id": identity, "revision": changed["revision"], "fingerprint": opened["fingerprint"]})
    assert load_workbook(saved["saved_path"]).active["C2"].value == "=B2*2"
    assert original.is_file()


def test_generate_keeps_composer_draft_and_reuses_accepted_submission(service):
    s = service
    draft = s.reviews.dispatch("reviews.create", {"conversation_id": "a", "kind": "skill", "message_id": "a-answer"})
    s.store.put_state("draft:a", {"text": "未发消息", "attachments": [{"path": "keep.txt"}]})
    before = s.store.versioned_state("draft:a")
    result = s.reviews.dispatch("reviews.generate", {"conversation_id": "a", "id": draft["id"], "revision": draft["revision"]})
    assert result["status"] == "generating"
    assert s.store.versioned_state("draft:a") == before
    s.reviews.dispatch("reviews.generate", {"conversation_id": "a", "id": draft["id"], "revision": result["revision"]})
    assert s.dispatch.call_count == 1
    submitted = s.dispatch.call_args.args[1]
    assert "review_draft" in submitted["text"]
    assert "draft_revision" not in submitted
    s.runs[result["run_id"]]["status"] = "interrupted"
    assert s.reviews.dispatch("reviews.get", {"conversation_id": "a", "id": draft["id"]})["generation_error"]
    retry = s.reviews.dispatch("reviews.generate", {"conversation_id": "a", "id": draft["id"], "revision": result["revision"]})
    assert retry["run_id"] != result["run_id"]
    assert s.store.versioned_state("draft:a") == before


def test_tool_entry_uses_authenticated_claim_and_does_not_accept_conversation_argument(service):
    s = service
    s.request_agent = Mock(return_value={"status": "ok"})
    workbench = DesktopWorkbench(s)
    result = workbench.agent_request({"session_id": "a", "run_id": "trusted"}, {"token": "claim", "action": "review_draft", "arguments": {
        "conversation_id": "b", "kind": "skill", "content": {"skill_name": "trusted-draft", "description": "测试归属", "instructions_md": "核对输入。"}}})
    assert result["review"]["conversation_id"] == "a"
    assert not s.reviews.entries("b")
    s.request_agent.return_value = {"status": "unavailable"}
    result = workbench.agent_request({"session_id": "a", "run_id": "trusted"}, {"token": "claim", "action": "review_draft", "arguments": {"kind": "skill"}})
    assert result["status"] == "cancelled"
    assert len(s.reviews.entries("a")) == 1


def test_stale_editor_revision_and_resource_traversal_are_rejected(service):
    s = service
    identity = skill(s)["review"]["id"]
    value = s.reviews.get("a", identity)
    updated = s.reviews.dispatch("reviews.update", {"conversation_id": "a", "id": identity, "revision": value["revision"], "value": {"title": "用户编辑"}})
    with pytest.raises(ValueError, match="草稿已更新"):
        s.reviews.dispatch("reviews.update", {"conversation_id": "a", "id": identity, "revision": value["revision"], "value": {"title": "迟到编辑"}})
    assert s.reviews.get("a", identity)["title"] == "用户编辑"
    with pytest.raises(ValueError, match="资源路径"):
        s.reviews.dispatch("reviews.update", {"conversation_id": "a", "id": identity, "revision": updated["revision"], "value": {
            "content": {**updated["content"], "resources": [{"path": "references/../../escape.md", "kind": "reference", "content": "bad"}]}}})


def test_rejected_submission_does_not_create_a_blank_review(service):
    with pytest.raises(ValueError, match="补齐"):
        skill(service, instructions_md="")
    assert service.reviews.entries("a") == {}


@pytest.mark.parametrize("extension", ["docx", "pptx"])
def test_office_review_publishes_real_word_and_slides_without_rewriting_them(service, extension):
    original = service.workspace("a") / ("来源." + extension)
    if extension == "docx":
        from docx import Document
        document = Document()
        document.add_heading("访谈分析", 0)
        document.add_paragraph("结论均应回溯来源。")
        document.save(original)
    else:
        from pptx import Presentation
        document = Presentation()
        slide = document.slides.add_slide(document.slide_layouts[0])
        slide.shapes.title.text = "访谈分析"
        document.save(original)
    receipt = service.reviews.submit_tool("a", "office", {"kind": "office", "file_path": str(original), "title": "审阅成果"})
    identity = receipt["review"]["id"]
    entry = service.reviews.get("a", identity)
    opened = service.reviews.dispatch("reviews.open", {"conversation_id": "a", "id": identity})
    assert opened["payload"]["preview_kind"] == "office"
    assert opened["payload"]["read_only"]
    saved = service.reviews.dispatch("reviews.save", {"conversation_id": "a", "id": identity, "revision": entry["revision"], "fingerprint": opened["fingerprint"]})
    assert Path(saved["saved_path"]).read_bytes() == original.read_bytes()


def test_real_rpc_agent_submits_review_tool_and_preserves_composer(tmp_path):
    import re
    import threading
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from test_desktop_service import ServiceClient

    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            last_user = max(i for i, message in enumerate(body["messages"]) if message["role"] == "user")
            text = str(body["messages"][last_user]["content"])
            match = re.search(r"草稿 ID：([a-f0-9]+)", text)
            names = [item["function"]["name"] for item in body.get("tools", [])]
            results = body["messages"][last_user + 1:]
            reviewed = any(message["role"] == "tool" and "awaiting_review" in str(message.get("content")) for message in results)
            if match and not reviewed:
                name, args = ("review_draft", {"kind": "skill", "draft_id": match[1], "content": {
                    "skill_name": "rpc-review-method", "description": "核对来源后生成可复用方法", "instructions_md": "## 方法\n核对原始来源。\n## 验证\n逐条回溯依据。"}}) if "review_draft" in names else ("tool_search", {"query": "review_draft", "limit": 1})
                delta = {"tool_calls": [{"index": 0, "id": "call_" + uuid.uuid4().hex, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
                finish = "tool_calls"
            else:
                delta, finish = {"content": "草稿已提交审阅。" if reviewed else "先核对来源，再形成结论。"}, "stop"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            for chunk in ({"id": "fixture", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}, {"id": "fixture", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}):
                self.wfile.write(("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = ServiceClient(tmp_path / "rpc")
    try:
        settings = client.call("settings.save_model", name="本地验证", model_name="fixture", base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="fixture", supports_tools=True)
        model = next(item for item in settings["models"] if item["configured"])
        cid = client.call("conversation.create")["id"]
        client.call("conversation.send", conversation_id=cid, request_id=uuid.uuid4().hex, message_id=uuid.uuid4().hex, model_id=model["id"], text="核对来源并总结方法", attachments=[], skills=[])
        source = client.wait_run(cid)
        assert source["run"]["status"] == "completed", source["run"]
        answer = next(message for message in reversed(source["messages"]) if message["role"] == "assistant")
        client.call("draft.save", conversation_id=cid, revision=source["draft_revision"], value={"text": "保留我的下一条输入", "attachments": []})
        draft = client.call("reviews.create", conversation_id=cid, kind="skill", message_id=answer["id"])
        client.call("reviews.generate", conversation_id=cid, id=draft["id"], revision=draft["revision"])
        result = client.wait_run(cid)
        assert result["run"]["status"] == "completed", result["run"]
        assert result["draft"]["text"] == "保留我的下一条输入"
        submitted = next(message for message in result["messages"] if message["id"] == result["run"]["message_id"])
        assert "草稿 ID：" in submitted["content"]
        assert submitted["meta"]["display_content"].startswith("从本轮任务中提炼 Skill")
        assert "草稿 ID：" not in submitted["meta"]["display_content"]
        ready = client.call("reviews.get", conversation_id=cid, id=draft["id"])
        assert ready["status"] == "ready", ready
        assert ready["run_id"] == result["run"]["id"]
        summary = next(item for item in result["reviews"] if item["id"] == draft["id"])
        assert summary["run_id"] == ready["run_id"]
        assert summary["status"] == "ready"
        assert summary["kind"] == "skill"
        assert "source" not in summary and "content" not in summary and "submission" not in summary
        other = client.call("conversation.create")
        assert client.call("conversation.get", conversation_id=other["id"])["reviews"] == []
        assert any(event.get("event") == "review_changed" and event.get("data", {}).get("open") for event in client.events)
        saved = client.call("reviews.save", conversation_id=cid, id=draft["id"], revision=ready["revision"])
        assert Path(saved["saved_path"], "SKILL.md").is_file()
        assert any(item["name"] == "rpc-review-method" for item in client.call("skills.list"))
        assert any("review_draft" in [tool["function"]["name"] for tool in request.get("tools", [])] for request in requests)
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
