import ast
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.skill_from_conversation import (
    ConversationSkillCaptureRepository,
    build_evidence_source,
    build_skill_json,
    build_target_skill_snapshot,
    compile_conversation_skill_draft,
    compute_skill_revision,
    extract_conversation_skill_evidence,
    extract_python_script_assets,
    extract_impl_tool_refs,
    normalize_conversation_skill_evidence,
    normalize_skill_draft,
    render_session_transcript,
    save_new_skill,
    update_existing_skill_from_draft,
    validate_conversation_skill_draft,
    validate_impl_py,
)
from core.skill_manager import SkillManager


class FakeProvider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_stream(self, messages, tools=None):
        self.calls.append(messages)
        if not self.responses:
            raise AssertionError("Unexpected third model call")
        yield {"type": "content", "content": self.responses.pop(0)}


class TestSkillFromConversation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.temp_dir, "skills")
        self.ai_skills_dir = os.path.join(self.temp_dir, "ai_skills")
        os.makedirs(self.skills_dir, exist_ok=True)
        os.makedirs(self.ai_skills_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _manager(self):
        manager = SkillManager(workspace_dir=self.temp_dir)
        manager.skills_dirs = [self.skills_dir, self.ai_skills_dir]
        manager.load_skills()
        return manager

    def _create_existing_skill(self, name="ops-guide"):
        skill_dir = os.path.join(self.ai_skills_dir, name)
        os.makedirs(skill_dir, exist_ok=True)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as handle:
            handle.write(
                "---\n"
                f"name: {name}\n"
                "description: Existing operations guide\n"
                "kind: knowledge\n"
                "experience: []\n"
                "---\n\n"
                "# Skill Purpose\nExisting body.\n"
            )
        with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": 2,
                    "name": name,
                    "kind": "knowledge",
                    "description": "Existing operations guide",
                    "tags": ["existing"],
                    "triggers": ["existing trigger"],
                    "tool_refs": [],
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
        return skill_dir

    def test_render_session_transcript_includes_messages_and_tool_calls(self):
        transcript = render_session_transcript(
            "session-1",
            "整理图片",
            [
                {"role": "user", "content": "按日期整理图片"},
                {
                    "role": "assistant",
                    "content": "我会写脚本处理。",
                    "tool_calls": [{"function": {"name": "bash", "arguments": "{}"}}],
                },
            ],
            meta={"workspace_dir": "D:/workspace"},
        )

        self.assertIn("# 会话: 整理图片", transcript)
        self.assertIn("- 工作区: D:/workspace", transcript)
        self.assertIn("按日期整理图片", transcript)
        self.assertIn("[tool_calls]", transcript)
        self.assertIn("bash", transcript)

    def test_normalize_draft_fills_defaults_and_derives_tool_refs(self):
        draft = normalize_skill_draft(
            {
                "skill_name": "Image Sorter!",
                "description": "Sort images by date",
                "impl_py": "def sort_images(path):\n    return path\n",
            }
        )

        self.assertEqual(draft["skill_name"], "image-sorter")
        self.assertEqual(draft["tool_refs"], ["sort_images"])
        self.assertTrue(draft["usage_guidelines"])

    def test_impl_validation_accepts_valid_code_and_rejects_bad_code(self):
        ok, error = validate_impl_py("def hello(name):\n    return name\n")
        self.assertTrue(ok, error)

        ok, error = validate_impl_py("def broken(:\n    pass\n")
        self.assertFalse(ok)
        self.assertIn("syntax error", error)

        ok, error = validate_impl_py("print('side effect')\n")
        self.assertFalse(ok)
        self.assertIn("top-level", error)

    def test_extract_python_script_assets_from_run_python_code(self):
        assets = extract_python_script_assets(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "run_python_code",
                                "arguments": json.dumps({"code": "print('ok')\n", "cwd": "D:/work"}),
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": "ok\n"},
            ]
        )

        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["name"], "run_python_001")
        self.assertEqual(assets[0]["path"], "scripts/run_python_001.py")
        self.assertEqual(assets[0]["runtime"], "python")
        self.assertTrue(assets[0]["valid"])
        self.assertIn("print('ok')", assets[0]["code"])
        self.assertEqual(assets[0]["source_tool_call_id"], "call-1")

    def test_save_new_skill_without_impl_creates_knowledge_package(self):
        result = save_new_skill(
            {
                "skill_name": "meeting-notes",
                "description": "Capture meeting note patterns",
                "description_cn": "沉淀会议纪要经验",
                "usage_guidelines": "Use for recurring meeting summaries.",
                "experience_items": ["Keep action items separate."],
                "tags": ["meeting"],
                "triggers": ["meeting notes"],
            },
            target_root=self.ai_skills_dir,
        )

        self.assertTrue(result.ok, result.message)
        self.assertTrue(os.path.exists(os.path.join(result.path, "SKILL.md")))
        self.assertTrue(os.path.exists(os.path.join(result.path, "skill.json")))
        self.assertFalse(os.path.exists(os.path.join(result.path, "impl.py")))
        payload = build_skill_json({"skill_name": "meeting-notes", "description": "x"})
        self.assertEqual(payload["source_format"], "cowork")

    def test_save_new_skill_with_valid_impl_creates_tool_package(self):
        result = save_new_skill(
            {
                "skill_name": "path-helper",
                "description": "Reusable path helper",
                "usage_guidelines": "Use for path formatting.",
                "impl_py": "def normalize_path(path):\n    return path.replace('\\\\', '/')\n",
            },
            target_root=self.ai_skills_dir,
        )

        self.assertTrue(result.ok, result.message)
        impl_path = os.path.join(result.path, "impl.py")
        self.assertTrue(os.path.exists(impl_path))
        with open(os.path.join(result.path, "skill.json"), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["tool_refs"], ["normalize_path"])

    def test_save_new_skill_with_script_assets_registers_script_entries(self):
        result = save_new_skill(
            {
                "skill_name": "captured-python",
                "description": "Captured Python helpers",
                "usage_guidelines": "Use the captured script when repeating the workflow.",
                "script_assets": [
                    {
                        "name": "run_python_001",
                        "path": "scripts/run_python_001.py",
                        "runtime": "python",
                        "description": "Captured code",
                        "code": "print('ok')\n",
                    }
                ],
            },
            target_root=self.ai_skills_dir,
        )

        self.assertTrue(result.ok, result.message)
        self.assertTrue(os.path.exists(os.path.join(result.path, "scripts", "run_python_001.py")))
        with open(os.path.join(result.path, "skill.json"), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["script_refs"], ["scripts/run_python_001.py"])
        self.assertEqual(payload["script_entries"][0]["name"], "run_python_001")
        self.assertEqual(payload["script_entries"][0]["runtime"], "python")

    def test_save_new_skill_rejects_invalid_impl_without_writing_package(self):
        result = save_new_skill(
            {
                "skill_name": "bad-helper",
                "description": "Bad helper",
                "impl_py": "def broken(:\n    pass\n",
            },
            target_root=self.ai_skills_dir,
        )

        self.assertFalse(result.ok)
        self.assertFalse(os.path.exists(os.path.join(self.ai_skills_dir, "bad-helper")))

    def test_update_existing_skill_appends_experience_and_merges_metadata(self):
        self._create_existing_skill()
        manager = self._manager()

        result = update_existing_skill_from_draft(
            manager,
            "ops-guide",
            {
                "description": "Updated operations guide",
                "experience_items": ["Capture stderr before retrying."],
                "tags": ["retry"],
                "triggers": ["stderr retry"],
                "anti_triggers": ["unrelated UI styling"],
            },
            strategy="append",
        )

        self.assertTrue(result.ok, result.message)
        entries = manager.get_experience_entries("ops-guide")
        self.assertEqual(len(entries), 1)
        self.assertIn("stderr", entries[0]["experience_text"])
        with open(os.path.join(self.ai_skills_dir, "ops-guide", "skill.json"), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertIn("existing", payload["tags"])
        self.assertIn("retry", payload["tags"])
        self.assertIn("stderr retry", payload["triggers"])
        self.assertIn("unrelated UI styling", payload["anti_triggers"])

        duplicate = update_existing_skill_from_draft(
            manager,
            "ops-guide",
            {
                "experience_items": ["Capture stderr before retrying."],
                "tags": ["retry"],
            },
            strategy="append_experience",
        )
        self.assertTrue(duplicate.ok, duplicate.message)
        self.assertEqual(len(manager.get_experience_entries("ops-guide")), 1)

    def test_update_existing_skill_appends_script_assets(self):
        self._create_existing_skill()
        manager = self._manager()

        result = update_existing_skill_from_draft(
            manager,
            "ops-guide",
            {
                "experience_items": ["Reuse the captured script."],
                "script_assets": [
                    {
                        "name": "run_python_001",
                        "path": "scripts/run_python_001.py",
                        "runtime": "python",
                        "description": "Captured code",
                        "code": "print('ok')\n",
                    }
                ],
            },
            strategy="append",
        )

        self.assertTrue(result.ok, result.message)
        self.assertTrue(os.path.exists(os.path.join(self.ai_skills_dir, "ops-guide", "scripts", "run_python_001.py")))
        with open(os.path.join(self.ai_skills_dir, "ops-guide", "skill.json"), "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertIn("scripts/run_python_001.py", payload["script_refs"])
        self.assertEqual(payload["script_entries"][0]["name"], "run_python_001")

    def test_update_existing_skill_rewrite_preserves_structured_entries(self):
        self._create_existing_skill()
        manager = self._manager()
        first = update_existing_skill_from_draft(
            manager,
            "ops-guide",
            {"experience_items": ["Preserve this lesson."]},
            strategy="append",
        )
        self.assertTrue(first.ok, first.message)

        result = update_existing_skill_from_draft(
            manager,
            "ops-guide",
            {
                "description": "Rewritten operations guide",
                "usage_guidelines": "New body guidance.",
                "experience_items": ["New summary lesson."],
            },
            strategy="rewrite",
        )

        self.assertTrue(result.ok, result.message)
        entries = manager.get_experience_entries("ops-guide")
        self.assertEqual(len(entries), 1)
        self.assertIn("Preserve this lesson.", entries[0]["experience_text"])
        with open(os.path.join(self.ai_skills_dir, "ops-guide", "SKILL.md"), "r", encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("Rewritten operations guide", content)
        self.assertIn("New body guidance.", content)

    def test_evidence_source_redacts_secrets_paths_and_reports_omissions(self):
        source = build_evidence_source(
            "session-1",
            "Deploy",
            [
                {
                    "id": f"m{index}",
                    "role": "user" if index % 2 else "assistant",
                    "content": (
                        "password=super-secret "
                        "D:\\code\\workspace\\project "
                        + ("x" * 500)
                    ),
                }
                for index in range(1, 9)
            ],
            meta={"workspace_dir": "D:\\code\\workspace\\project"},
            char_limit=1800,
        )

        self.assertNotIn("super-secret", source["text"])
        self.assertNotIn("D:\\code\\workspace\\project", source["text"])
        self.assertIn("<redacted-secret>", source["text"])
        self.assertIn("<workspace>", source["text"])
        self.assertTrue(source["omitted_message_ids"])
        self.assertIn("[明确裁剪]", source["text"])

    def test_evidence_source_omits_executed_python_before_script_rewrite(self):
        source = build_evidence_source(
            "session-1",
            "Process files",
            [
                {
                    "id": "m1",
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "run_python_code",
                                "arguments": json.dumps(
                                    {
                                        "code": "from pathlib import Path\nprint(Path('fixed').read_text())",
                                        "cwd": "D:/work",
                                    }
                                ),
                            },
                        }
                    ],
                }
            ],
        )

        self.assertNotIn("Path('fixed')", source["text"])
        self.assertNotIn("D:/work", source["text"])
        self.assertIn("<executed-code-omitted; rewrite from purpose>", source["text"])

    def test_evidence_normalization_rejects_unknown_refs_and_lowers_confidence(self):
        evidence = normalize_conversation_skill_evidence(
            {
                "task_goal": {"text": "Build reports", "source_message_ids": ["m1"]},
                "outcome": {"text": "Report passed", "source_message_ids": ["m2"]},
                "reusable_patterns": [{"text": "Validate first", "source_message_ids": ["missing"]}],
                "workflow_steps": [{"text": "Run validation", "source_message_ids": ["m2"]}],
                "verification_methods": [{"text": "Check output", "source_message_ids": ["m2"]}],
            },
            {
                "source_message_ids": ["m1", "m2"],
                "included_message_ids": ["m1", "m2"],
                "omitted_message_ids": [],
                "privacy_findings": [],
                "source_digest": "digest",
            },
        )

        self.assertEqual(evidence["confidence"], "low")
        self.assertEqual(evidence["invalid_source_refs"], ["missing"])
        self.assertEqual(evidence["reusable_patterns"][0]["source_message_ids"], [])

    def test_two_stage_generation_uses_exactly_two_calls_and_compiler_has_no_raw_transcript(self):
        provider = FakeProvider(
            [
                json.dumps(
                    {
                        "task_goal": {"text": "Create a report", "source_message_ids": ["m1"]},
                        "outcome": {"text": "Report verified", "source_message_ids": ["m2"]},
                        "reusable_patterns": [{"text": "Validate inputs", "source_message_ids": ["m1"]}],
                        "workflow_steps": [{"text": "Generate then validate", "source_message_ids": ["m2"]}],
                        "verification_methods": [{"text": "Open output", "source_message_ids": ["m2"]}],
                        "suggested_name": "create-report",
                    }
                ),
                json.dumps(
                    {
                        "skill_name": "create-report",
                        "description": "Create verified reports when a user requests a repeatable reporting workflow.",
                        "description_cn": "在需要可重复报告流程时创建并验证报告。",
                        "tags": ["report"],
                        "triggers": ["create report"],
                        "anti_triggers": ["one-off chat summary"],
                        "instructions_md": "1. Validate inputs.\\n2. Generate the report.\\n3. Verify the output.",
                        "workflow": ["Validate inputs", "Generate report", "Verify output"],
                        "experience_items": ["Verify generated files."],
                        "resources": [],
                        "change_summary": ["Create a knowledge-first Skill."],
                    }
                ),
            ]
        )
        source = build_evidence_source(
            "s1",
            "Report",
            [
                {"id": "m1", "role": "user", "content": "RAW_TRANSCRIPT_MARKER create a report"},
                {"id": "m2", "role": "assistant", "content": "Verified the report"},
            ],
        )
        evidence = extract_conversation_skill_evidence(provider, source)
        draft = compile_conversation_skill_draft(
            provider,
            evidence,
            capture_id="capture-1",
            source_session_id="s1",
        )

        self.assertEqual(len(provider.calls), 2)
        compiler_input = provider.calls[1][1]["content"]
        self.assertNotIn("RAW_TRANSCRIPT_MARKER", compiler_input)
        self.assertEqual(draft["quality"], "high")
        self.assertIn("when a user requests", draft["description"])

    def test_static_validation_blocks_secrets_bad_resources_and_invalid_code(self):
        validation = validate_conversation_skill_draft(
            {
                "mode": "create",
                "skill_name": "unsafe-skill",
                "description": "Use when processing reports.",
                "instructions_md": "password=visible-secret",
                "anti_triggers": ["unrelated tasks"],
                "resources": [
                    {
                        "kind": "script",
                        "path": "../escape.py",
                        "content": "print('side effect')",
                        "source_message_ids": ["outside"],
                    },
                    {
                        "kind": "script",
                        "path": "scripts/fetch.py",
                        "content": "import requests\n\ndef fetch(url):\n    return requests.get(url).text",
                        "source_message_ids": ["m1"],
                    }
                ],
                "source_message_ids": ["m1"],
            },
            allowed_source_ids=["m1"],
        )

        self.assertFalse(validation["ok"])
        codes = {item["code"] for item in validation["issues"]}
        self.assertIn("secret_literal", codes)
        self.assertIn("invalid_resource_path", codes)
        self.assertIn("undeclared_python_dependency", codes)

    def test_capture_repository_restores_pending_and_minimizes_saved_record(self):
        repository = ConversationSkillCaptureRepository(
            root_dir=os.path.join(self.temp_dir, "captures")
        )
        capture = repository.create("session-1", ["m1", "m2"])
        capture["phase"] = "analysis_ready"
        capture["evidence"] = {"task_goal": {"text": "Reusable goal"}}
        repository.save(capture)

        restored = repository.list_for_session("session-1")
        self.assertEqual(restored[0]["phase"], "analysis_ready")
        self.assertTrue(repository.mark_saved(capture["capture_id"], "saved-skill"))
        saved = repository.load(capture["capture_id"])
        self.assertEqual(saved["phase"], "saved")
        self.assertEqual(saved["evidence"], {})
        self.assertEqual(saved.get("target_snapshot"), {})
        self.assertEqual(saved["saved_skill_name"], "saved-skill")

    def test_update_rejects_target_revision_conflict(self):
        skill_dir = self._create_existing_skill()
        manager = self._manager()
        snapshot = build_target_skill_snapshot(manager.skill_records["ops-guide"])
        self.assertEqual(snapshot["revision"], compute_skill_revision(skill_dir))
        with open(os.path.join(skill_dir, "SKILL.md"), "a", encoding="utf-8") as handle:
            handle.write("\nExternal edit.\n")

        result = update_existing_skill_from_draft(
            manager,
            "ops-guide",
            {
                "target_revision": snapshot["revision"],
                "experience_items": ["New lesson"],
            },
            strategy="append_experience",
        )

        self.assertFalse(result.ok)
        self.assertIn("changed after compilation", result.message)

    def test_main_defines_conversation_skill_dialogs_used_by_flow(self):
        main_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        class_names = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}

        self.assertIn("ConversationSkillOptionsDialog", class_names)
        self.assertIn("ConversationSkillRangeDialog", class_names)
        self.assertIn("ConversationSkillEvidenceDialog", class_names)
        self.assertIn("ConversationSkillPreviewDialog", class_names)

    def test_conversation_skill_options_hides_update_fields_when_creating(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import main

        app = QApplication.instance() or QApplication([])
        self.addCleanup(app.processEvents)
        dialog = main.ConversationSkillOptionsDialog(
            [{"name": "PPTX-Template-Skills", "description": "Template skill"}]
        )
        self.addCleanup(dialog.deleteLater)

        self.assertEqual(dialog.mode_combo.currentData(), "create")
        self.assertTrue(dialog.target_label.isHidden())
        self.assertTrue(dialog.target_combo.isHidden())
        self.assertTrue(dialog.strategy_label.isHidden())
        self.assertTrue(dialog.strategy_combo.isHidden())
        self.assertEqual(dialog.selected_options()["target_skill"], "")

        dialog.mode_combo.setCurrentIndex(1)
        self.assertFalse(dialog.target_label.isHidden())
        self.assertFalse(dialog.target_combo.isHidden())
        self.assertFalse(dialog.strategy_label.isHidden())
        self.assertFalse(dialog.strategy_combo.isHidden())
        self.assertEqual(dialog.selected_options()["target_skill"], "PPTX-Template-Skills")

        dialog.mode_combo.setCurrentIndex(0)
        self.assertTrue(dialog.target_label.isHidden())
        self.assertTrue(dialog.target_combo.isHidden())
        self.assertTrue(dialog.strategy_label.isHidden())
        self.assertTrue(dialog.strategy_combo.isHidden())

    def test_conversation_skill_range_uses_clear_checkable_selection(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import main

        app = QApplication.instance() or QApplication([])
        self.addCleanup(app.processEvents)
        dialog = main.ConversationSkillRangeDialog(
            [
                {"role": "user", "content": "沉淀这个流程"},
                {
                    "role": "assistant",
                    "content": "自动技能上下文",
                    "meta": {"kind": "skill_context", "source": "skill_prompt"},
                },
                {"role": "assistant", "content": "这是可复用经验"},
            ]
        )
        self.addCleanup(dialog.deleteLater)

        self.assertIsInstance(dialog.message_list.itemDelegate(), main.AppleCheckableListDelegate)
        self.assertEqual(dialog.message_list.selectionMode(), main.QAbstractItemView.NoSelection)
        first_index = dialog.message_list.model().index(0, 0)
        self.assertTrue(dialog.message_list.itemDelegate()._is_checked(first_index.data(main.Qt.CheckStateRole)))
        self.assertEqual(len(dialog.selected_messages()), 2)
        self.assertIn("已选择 2 条消息", dialog.selection_hint.text())

        dialog.message_list.item(0).setCheckState(main.Qt.Unchecked)
        self.assertEqual(len(dialog.selected_messages()), 1)
        self.assertIn("已选择 1 条消息", dialog.selection_hint.text())

    def test_conversation_skill_evidence_dialog_defaults_to_create_and_no_resources(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import main

        app = QApplication.instance() or QApplication([])
        self.addCleanup(app.processEvents)

        dialog = main.ConversationSkillEvidenceDialog(
            {
                "confidence": "low",
                "task_goal": {"text": "Build reports", "source_message_ids": ["m1"]},
                "reusable_patterns": [{"text": "Validate inputs", "source_message_ids": ["m1"]}],
                "missing_evidence": ["Missing output verification"],
                "resource_candidates": [
                    {
                        "id": "resource-1",
                        "kind": "script",
                        "description": "Parameterize report generation",
                        "source_message_ids": ["m1"],
                    }
                ],
            },
            [{"name": "report-skill", "description": "Existing report skill"}],
        )
        self.addCleanup(dialog.deleteLater)

        destination = dialog.selected_destination()
        self.assertEqual(destination["mode"], "create")
        self.assertEqual(destination["selected_resources"], [])
        self.assertEqual(dialog.objectName(), "ConversationSkillEvidenceDialog")
        self.assertTrue(dialog.findChildren(main.ProductActionBar))

    def test_conversation_skill_range_keeps_dialog_open_when_background_handoff_fails(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QDialog
        import main

        app = QApplication.instance() or QApplication([])
        self.addCleanup(app.processEvents)

        def fail_handoff(_messages):
            raise RuntimeError("provider unavailable")

        dialog = main.ConversationSkillRangeDialog(
            [{"id": "m1", "role": "user", "content": "沉淀这个流程"}],
            submit_handler=fail_handoff,
        )
        self.addCleanup(dialog.deleteLater)

        dialog._accept_if_valid()

        self.assertNotEqual(dialog.result(), QDialog.Accepted)
        self.assertFalse(dialog._submitting)
        self.assertEqual(dialog.next_btn.text(), "开始复用分析")
        self.assertFalse(dialog.submit_error.isHidden())
        self.assertIn("provider unavailable", dialog.submit_error.text())

    def test_conversation_skill_evidence_handoff_receives_confirmed_destination(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QDialog
        import main

        app = QApplication.instance() or QApplication([])
        self.addCleanup(app.processEvents)
        received = []
        dialog = main.ConversationSkillEvidenceDialog(
            {
                "confidence": "high",
                "task_goal": {"text": "Build reports", "source_message_ids": ["m1"]},
                "reusable_patterns": [{"text": "Validate inputs", "source_message_ids": ["m1"]}],
            },
            [],
            submit_handler=lambda destination: received.append(destination) or True,
        )
        self.addCleanup(dialog.deleteLater)

        dialog._accept_if_valid()

        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(received[0]["mode"], "create")
        self.assertEqual(received[0]["selected_resources"], [])

    def test_conversation_skill_evidence_keeps_dialog_open_when_compile_handoff_fails(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QDialog
        import main

        app = QApplication.instance() or QApplication([])
        self.addCleanup(app.processEvents)

        def fail_compile(_destination):
            raise RuntimeError("compile unavailable")

        dialog = main.ConversationSkillEvidenceDialog(
            {
                "confidence": "high",
                "task_goal": {"text": "Build reports", "source_message_ids": ["m1"]},
                "reusable_patterns": [{"text": "Validate inputs", "source_message_ids": ["m1"]}],
            },
            [],
            submit_handler=fail_compile,
        )
        self.addCleanup(dialog.deleteLater)

        dialog._accept_if_valid()

        self.assertNotEqual(dialog.result(), QDialog.Accepted)
        self.assertFalse(dialog._submitting)
        self.assertEqual(dialog.compile_btn.text(), "后台编译 Skill 草稿")
        self.assertFalse(dialog.submit_error.isHidden())
        self.assertIn("compile unavailable", dialog.submit_error.text())

    def test_conversation_skill_status_row_exposes_running_and_pending_states(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import main

        app = QApplication.instance() or QApplication([])
        self.addCleanup(app.processEvents)
        row = main.ConversationSkillStatusRow()
        self.addCleanup(row.deleteLater)

        row.set_capture_state(
            "正在分析复用价值",
            detail="已转到后台，可继续对话",
            running=True,
        )
        self.assertTrue(row.activity._running)
        self.assertTrue(row.status_icon.isHidden())
        self.assertTrue(row.review_btn.isHidden())
        self.assertEqual(row.detail_label.text(), "已转到后台，可继续对话")

        row.set_capture_state(
            "复用分析已完成",
            detail="待确认",
            pending=True,
            action_text="继续确认",
        )
        self.assertFalse(row.activity._running)
        self.assertFalse(row.status_icon.isHidden())
        self.assertFalse(row.review_btn.isHidden())
        self.assertEqual(row.review_btn.text(), "继续确认")

        sidebar_indicator = main.SessionSkillCaptureIndicator()
        self.addCleanup(sidebar_indicator.deleteLater)
        sidebar_indicator.set_phase("compiling")
        self.assertFalse(sidebar_indicator.isHidden())
        self.assertEqual(sidebar_indicator._phase, "compiling")
        sidebar_indicator.set_phase("draft_ready")
        self.assertEqual(sidebar_indicator._phase, "draft_ready")
        sidebar_indicator.set_phase("")
        self.assertTrue(sidebar_indicator.isHidden())

    def test_conversation_skill_completion_toast_only_for_cross_session_and_deduplicates(self):
        import main

        add_toast = Mock()
        window = SimpleNamespace(
            current_session_id="source-session",
            _conversation_skill_completion_toast_keys=set(),
            _resolved_session_title=lambda _state: "客户流失分析",
            add_system_toast=add_toast,
        )
        state = SimpleNamespace(session_id="source-session")

        same_session = main.MainWindow._notify_cross_session_skill_completion(
            window,
            state,
            "capture-1",
            "analysis_ready",
        )
        self.assertFalse(same_session)
        add_toast.assert_not_called()

        window.current_session_id = "other-session"
        first = main.MainWindow._notify_cross_session_skill_completion(
            window,
            state,
            "capture-1",
            "analysis_ready",
        )
        duplicate = main.MainWindow._notify_cross_session_skill_completion(
            window,
            state,
            "capture-1",
            "analysis_ready",
        )
        draft_ready = main.MainWindow._notify_cross_session_skill_completion(
            window,
            state,
            "capture-1",
            "draft_ready",
        )
        failed = main.MainWindow._notify_cross_session_skill_completion(
            window,
            state,
            "capture-1",
            "failed",
        )

        self.assertTrue(first)
        self.assertFalse(duplicate)
        self.assertTrue(draft_ready)
        self.assertFalse(failed)
        self.assertEqual(add_toast.call_count, 2)
        self.assertIn("复用分析已完成", add_toast.call_args_list[0].args[0])
        self.assertIn("Skill 草稿已生成", add_toast.call_args_list[1].args[0])
        self.assertTrue(all(call.kwargs["session_id"] == "source-session" for call in add_toast.call_args_list))

    def test_session_skill_picker_uses_clear_checkable_selection(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtTest import QTest
        import main

        app = QApplication.instance() or QApplication([])
        self.addCleanup(app.processEvents)
        dialog = main.SessionSkillPickerDialog(
            [
                {"name": "alpha-skill", "description": "Alpha", "type": "ai_generated"},
                {"name": "beta-skill", "description": "Beta", "type": "ai_generated"},
            ],
            selected_skill_names=["alpha-skill"],
        )
        self.addCleanup(dialog.deleteLater)

        self.assertIsInstance(dialog.skill_list.itemDelegate(), main.AppleCheckableListDelegate)
        self.assertEqual(dialog.skill_list.selectionMode(), main.QAbstractItemView.NoSelection)
        self.assertEqual(dialog.selected_skill_names(), ["alpha-skill"])

        dialog.skill_list.item(1).setCheckState(main.Qt.Checked)
        self.assertEqual(dialog.selected_skill_names(), ["alpha-skill", "beta-skill"])
        self.assertIn("当前已指定 2 个能力", dialog.selection_hint.text())

        dialog.clear_selection()
        dialog.show()
        app.processEvents()
        item_rect = dialog.skill_list.visualItemRect(dialog.skill_list.item(0))
        QTest.mouseClick(dialog.skill_list.viewport(), main.Qt.LeftButton, pos=item_rect.center())
        app.processEvents()
        self.assertEqual(dialog.selected_skill_names(), ["alpha-skill"])
        QTest.mouseClick(dialog.skill_list.viewport(), main.Qt.LeftButton, pos=item_rect.center())
        app.processEvents()
        self.assertEqual(dialog.selected_skill_names(), [])


if __name__ == "__main__":
    unittest.main()
