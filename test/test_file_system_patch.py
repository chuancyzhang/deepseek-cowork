import codecs
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch as mock_patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import apply_patch as patch_module
from core.apply_patch import MAX_PATCH_FILES, apply_patch
from core.filesystem_ops import MAX_TEXT_FILE_BYTES, glob_paths, grep_contents, read_text_file
from core.llm.providers import API_PROTOCOL_CHAT_COMPLETIONS, AnthropicProvider, OpenAIProvider


def _load_file_skill_module():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    module_path = os.path.join(repo_root, "skills", "file-system", "impl.py")
    spec = importlib.util.spec_from_file_location("file_system_patch_impl_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Signal:
    def __init__(self):
        self.events = []

    def emit(self, payload):
        self.events.append(payload)


class _GodModeConfig:
    def get_god_mode(self):
        return True


class TestFileSystemPatch(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.mkdtemp()
        self.context = {}

    def tearDown(self):
        shutil.rmtree(self.workspace, ignore_errors=True)

    def _path(self, relative):
        return os.path.join(self.workspace, relative)

    def _write_bytes(self, relative, data):
        absolute = self._path(relative)
        os.makedirs(os.path.dirname(absolute) or self.workspace, exist_ok=True)
        with open(absolute, "wb") as handle:
            handle.write(data)

    def _read_full(self, relative, encoding=None):
        result = read_text_file(
            self.workspace,
            relative,
            encoding=encoding,
            context=self.context,
            action="text_file_read",
        )
        self.assertTrue(result["ok"], result)
        return result

    def _read_bytes(self, relative):
        with open(self._path(relative), "rb") as handle:
            return handle.read()

    def _read_text(self, relative, encoding="utf-8"):
        with open(self._path(relative), "r", encoding=encoding, newline="") as handle:
            return handle.read()

    def test_read_metadata_paging_and_strict_explicit_encoding(self):
        self._write_bytes("utf8.txt", codecs.BOM_UTF8 + b"one\r\ntwo\r\n")

        paged = read_text_file(
            self.workspace,
            "utf8.txt",
            offset=1,
            limit=1,
            context=self.context,
            action="text_file_read",
        )
        self.assertFalse(paged["audit_complete"])
        self.assertEqual(paged["next_offset"], 2)
        self.assertEqual(paged["encoding"], "utf-8")
        self.assertTrue(paged["bom"])
        self.assertEqual(paged["newline"], "\r\n")
        self.assertEqual(len(paged["sha256"]), 64)

        denied = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: utf8.txt\n@@\n-one\n+ONE\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(denied["error"]["code"], "read_required")

        legacy_bytes = "旧值\r\n".encode("gbk")
        self._write_bytes("legacy.txt", legacy_bytes)
        undecodable = read_text_file(self.workspace, "legacy.txt", context=self.context)
        self.assertEqual(undecodable["error"]["code"], "encoding_required")
        legacy = self._read_full("legacy.txt", encoding="gbk")
        self.assertEqual(legacy["content"], "旧值\r\n")
        audit = next(iter(self.context["file_state"]["reads"].values()))
        self.assertEqual(audit["sha256"], legacy["sha256"])
        self.assertEqual(audit["size"], len(legacy_bytes))
        self.assertIsInstance(audit["mtime_ns"], int)
        self.assertEqual(audit["encoding"], "gbk")
        self.assertEqual(audit["bom_hex"], "")
        self.assertEqual(audit["newline"], "\r\n")

    def test_update_preserves_bom_encoding_and_newlines(self):
        self._write_bytes("utf8.txt", codecs.BOM_UTF8 + b"one\r\ntwo\r\n")
        self._read_full("utf8.txt")
        result = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: utf8.txt\n@@\n-one\n+ONE\n*** End Patch",
            context=self.context,
        )
        self.assertTrue(result["ok"], result)
        raw = self._read_bytes("utf8.txt")
        self.assertTrue(raw.startswith(codecs.BOM_UTF8))
        self.assertEqual(raw, codecs.BOM_UTF8 + b"ONE\r\ntwo\r\n")

        self._write_bytes("legacy.txt", "旧值\r\n".encode("gbk"))
        self._read_full("legacy.txt", encoding="gbk")
        result = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: legacy.txt\n@@\n-旧值\n+新值\n*** End Patch",
            context=self.context,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(self._read_bytes("legacy.txt").decode("gbk"), "新值\r\n")

    def test_bounded_read_covering_entire_file_grants_write_audit(self):
        self._write_bytes("bounded.txt", b"one\ntwo\n")

        complete = read_text_file(
            self.workspace,
            "bounded.txt",
            offset=1,
            limit=2,
            context=self.context,
            action="text_file_read",
        )
        self.assertTrue(complete["audit_complete"])
        self.assertFalse(complete["truncated"])

        result = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: bounded.txt\n@@\n-one\n+ONE\n*** End Patch",
            context=self.context,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(self._read_text("bounded.txt"), "ONE\ntwo\n")

    def test_add_multi_hunk_eof_and_continuous_updates(self):
        added = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Add File: notes.txt\n+alpha\n+beta\n+gamma\n*** End Patch",
            context=self.context,
        )
        self.assertTrue(added["ok"], added)
        self.assertEqual(self._read_bytes("notes.txt"), b"alpha\nbeta\ngamma\n")

        self._read_full("notes.txt")
        updated = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: notes.txt\n@@\n-alpha\n+ALPHA\n@@\n-gamma\n+GAMMA\n@@\n+tail\n*** End of File\n*** End Patch",
            context=self.context,
        )
        self.assertTrue(updated["ok"], updated)
        self.assertEqual(self._read_text("notes.txt"), "ALPHA\nbeta\nGAMMA\ntail\n")

        second = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: notes.txt\n@@\n-beta\n+BETA\n*** End Patch",
            context=self.context,
        )
        self.assertTrue(second["ok"], second)

        padded = apply_patch(
            self.workspace,
            "*** Begin Patch \n  *** Update File: notes.txt\n@@\n-BETA\n+beta\n"
            "@@\n+after-padded-marker\n*** End of File\n\n *** End Patch",
            context=self.context,
        )
        self.assertTrue(padded["ok"], padded)
        self.assertIn("beta\n", self._read_text("notes.txt"))
        self.assertTrue(self._read_text("notes.txt").endswith("after-padded-marker\n"))

    def test_exact_unique_matching_and_stale_hash_not_mtime(self):
        self._write_bytes("repeat.txt", b"same\nsame\n")
        self._read_full("repeat.txt")
        ambiguous = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: repeat.txt\n@@\n-same\n+changed\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(ambiguous["error"]["code"], "ambiguous_hunk")

        self._write_bytes("unicode.txt", "café\nvalue  \n".encode("utf-8"))
        self._read_full("unicode.txt")
        unicode_fuzzy = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: unicode.txt\n@@\n-cafe\u0301\n+changed\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(unicode_fuzzy["error"]["code"], "context_not_found")
        whitespace_fuzzy = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: unicode.txt\n@@\n-value \n+changed\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(whitespace_fuzzy["error"]["code"], "context_not_found")

        self._write_bytes("stale.txt", b"old\n")
        original_stat = os.stat(self._path("stale.txt"))
        self._read_full("stale.txt")
        self._write_bytes("stale.txt", b"new\n")
        os.utime(
            self._path("stale.txt"),
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        stale = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: stale.txt\n@@\n-old\n+OLD\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(stale["error"]["code"], "stale_write")

    def test_move_rules_and_content_move_audit(self):
        self._write_bytes("plain.txt", b"plain\n")
        self._write_bytes("occupied.txt", b"occupied\n")
        occupied = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: plain.txt\n*** Move to: occupied.txt\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(occupied["error"]["code"], "destination_exists")
        moved = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: plain.txt\n*** Move to: moved.txt\n*** End Patch",
            context=self.context,
        )
        self.assertTrue(moved["ok"], moved)
        self.assertFalse(os.path.exists(self._path("plain.txt")))
        self.assertTrue(os.path.exists(self._path("moved.txt")))

        self._write_bytes("content.txt", b"old\n")
        denied = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: content.txt\n*** Move to: content-moved.txt\n@@\n-old\n+new\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(denied["error"]["code"], "read_required")
        self._read_full("content.txt")
        moved = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Update File: content.txt\n*** Move to: content-moved.txt\n@@\n-old\n+new\n*** End Patch",
            context=self.context,
        )
        self.assertTrue(moved["ok"], moved)
        self.assertEqual(self._read_text("content-moved.txt"), "new\n")

    def test_delete_decline_and_preflight_failure_apply_nothing(self):
        self._write_bytes("delete.txt", b"delete\n")
        self._write_bytes("delete-too.txt", b"delete\n")
        confirmation_paths = []

        def decline(paths):
            confirmation_paths.extend(paths)
            return False

        declined = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Add File: added.txt\n+added\n*** Delete File: delete.txt\n*** Delete File: delete-too.txt\n*** End Patch",
            context=self.context,
            confirm_delete=decline,
        )
        self.assertEqual(declined["error"]["code"], "cancelled")
        self.assertEqual(confirmation_paths, ["delete.txt", "delete-too.txt"])
        self.assertFalse(os.path.exists(self._path("added.txt")))
        self.assertTrue(os.path.exists(self._path("delete.txt")))
        self.assertTrue(os.path.exists(self._path("delete-too.txt")))

        failed = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Add File: untouched.txt\n+value\n*** Update File: missing.txt\n@@\n-old\n+new\n*** End Patch",
            context=self.context,
        )
        self.assertFalse(failed["ok"])
        self.assertFalse(os.path.exists(self._path("untouched.txt")))

    def test_successful_mixed_patch_preserves_patch_order(self):
        self._write_bytes("update.txt", b"old\n")
        self._write_bytes("move.txt", b"move\n")
        self._write_bytes("delete.txt", b"delete\n")
        self._read_full("update.txt")

        result = apply_patch(
            self.workspace,
            "*** Begin Patch\n"
            "*** Add File: add.txt\n+added\n"
            "*** Update File: update.txt\n@@\n-old\n+updated\n"
            "*** Update File: move.txt\n*** Move to: moved.txt\n"
            "*** Delete File: delete.txt\n"
            "*** End Patch",
            context=self.context,
            confirm_delete=lambda paths: paths == ["delete.txt"],
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            [item["change_type"] for item in result["changes"]],
            ["add", "update", "move", "delete"],
        )
        self.assertEqual(result["counts"], {"add": 1, "update": 1, "move": 1, "delete": 1})
        self.assertEqual(self._read_text("update.txt"), "updated\n")
        self.assertTrue(os.path.exists(self._path("moved.txt")))
        self.assertFalse(os.path.exists(self._path("move.txt")))
        self.assertFalse(os.path.exists(self._path("delete.txt")))

    def test_duplicate_missing_existing_and_structured_paths(self):
        duplicate = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Add File: duplicate.txt\n+one\n*** Add File: duplicate.txt\n+two\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(duplicate["error"]["code"], "duplicate_path")
        self.assertFalse(os.path.exists(self._path("duplicate.txt")))

        self._write_bytes("exists.txt", b"value\n")
        existing = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Add File: exists.txt\n+new\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(existing["error"]["code"], "destination_exists")
        structured = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Add File: report.docx\n+not really a document\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(structured["error"]["code"], "structured_document_not_supported")

    def test_limits_and_path_boundaries(self):
        operations = "\n".join(
            f"*** Add File: file-{index}.txt\n+value" for index in range(MAX_PATCH_FILES + 1)
        )
        too_many = apply_patch(
            self.workspace,
            f"*** Begin Patch\n{operations}\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(too_many["error"]["code"], "too_many_files")

        self._write_bytes("too-large.txt", b"x" * (MAX_TEXT_FILE_BYTES + 1))
        read_too_large = read_text_file(self.workspace, "too-large.txt", context=self.context)
        self.assertEqual(read_too_large["error"]["code"], "file_too_large")
        add_too_large = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Add File: add-too-large.txt\n+"
            + ("x" * (MAX_TEXT_FILE_BYTES + 1))
            + "\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(add_too_large["error"]["code"], "file_too_large")

        outside = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Add File: ../outside.txt\n+value\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(outside["error"]["code"], "path_outside_workspace")
        unc = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Add File: \\\\server\\share\\file.txt\n+value\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(unc["error"]["code"], "unc_path_rejected")

        with mock_patch(
            "core.filesystem_ops._is_reparse_point",
            side_effect=OSError("simulated path inspection failure"),
        ):
            inspection_failed = apply_patch(
                self.workspace,
                "*** Begin Patch\n*** Add File: inspection.txt\n+value\n*** End Patch",
                context=self.context,
            )
        self.assertEqual(inspection_failed["error"]["code"], "path_inspection_failed")
        self.assertFalse(os.path.exists(self._path("inspection.txt")))

        oversized = apply_patch(
            self.workspace,
            "x" * (patch_module.MAX_PATCH_BYTES + 1),
            context=self.context,
        )
        self.assertEqual(oversized["error"]["code"], "patch_too_large")

    def test_god_mode_retains_explicit_outside_workspace_authorization(self):
        with tempfile.TemporaryDirectory() as outside:
            destination = os.path.join(outside, "god-mode.txt")
            denied = apply_patch(
                self.workspace,
                f"*** Begin Patch\n*** Add File: {destination}\n+denied\n*** End Patch",
                context=self.context,
            )
            self.assertEqual(denied["error"]["code"], "path_outside_workspace")
            result = apply_patch(
                self.workspace,
                f"*** Begin Patch\n*** Add File: {destination}\n+allowed\n*** End Patch",
                context={"config_manager": _GodModeConfig()},
            )
            self.assertTrue(result["ok"], result)
            with open(destination, "r", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "allowed\n")

    def test_runtime_failure_reports_partial_apply(self):
        original_atomic = patch_module._atomic_write_bytes

        def flaky_atomic(path, data, **kwargs):
            if os.path.basename(path) == "second.txt":
                raise OSError("simulated commit failure")
            return original_atomic(path, data, **kwargs)

        with mock_patch.object(patch_module, "_atomic_write_bytes", side_effect=flaky_atomic):
            result = apply_patch(
                self.workspace,
                "*** Begin Patch\n*** Add File: first.txt\n+one\n*** Add File: second.txt\n+two\n*** Add File: third.txt\n+three\n*** End Patch",
                context=self.context,
            )

        self.assertEqual(result["error"]["code"], "partial_apply")
        self.assertEqual(result["applied_changes"][0]["path"], "first.txt")
        self.assertEqual(result["failed_change"]["path"], "second.txt")
        self.assertEqual(result["pending_changes"][0]["path"], "third.txt")
        self.assertTrue(os.path.exists(self._path("first.txt")))
        self.assertFalse(os.path.exists(self._path("second.txt")))

        with mock_patch.object(
            patch_module,
            "mark_file_written",
            side_effect=OSError("simulated audit state failure"),
        ):
            audit_failure = apply_patch(
                self.workspace,
                "*** Begin Patch\n*** Add File: audit-written.txt\n+value\n*** End Patch",
                context=self.context,
            )
        self.assertEqual(audit_failure["error"]["code"], "partial_apply")
        self.assertEqual(audit_failure["applied_changes"][0]["path"], "audit-written.txt")
        self.assertEqual(audit_failure["failed_change"]["failure_phase"], "audit_state")
        self.assertTrue(os.path.exists(self._path("audit-written.txt")))

    def test_diagnostics_do_not_include_patch_or_content(self):
        signal = _Signal()
        context = {"observability_signal": signal}
        result = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Add File: secret.txt\n+secret value\n*** End Patch",
            context=context,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual([event["status"] for event in signal.events], ["start", "preflight", "commit", "finish"])
        serialized = repr(signal.events)
        self.assertNotIn("secret value", serialized)
        self.assertNotIn("*** Begin Patch", serialized)

    def test_glob_and_grep_skip_reparse_points_with_warnings(self):
        target = self._path("target")
        link = self._path("link")
        os.makedirs(target)
        self._write_bytes("target/value.txt", b"needle\n")
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Directory symlink creation is unavailable")

        glob_result = glob_paths(self.workspace, pattern="*.txt")
        grep_result = grep_contents(self.workspace, pattern="needle")
        scoped_glob = glob_paths(self.workspace, pattern="*.txt", path="link")
        scoped_grep = grep_contents(self.workspace, pattern="needle", path="link")
        write_result = apply_patch(
            self.workspace,
            "*** Begin Patch\n*** Add File: link/blocked.txt\n+value\n*** End Patch",
            context=self.context,
        )
        self.assertEqual(glob_result["items"], ["target/value.txt"])
        self.assertEqual(grep_result["matches"][0]["path"], "target/value.txt")
        self.assertEqual(write_result["error"]["code"], "reparse_point_not_allowed")
        self.assertEqual(scoped_glob["items"], [])
        self.assertEqual(scoped_grep["matches"], [])
        self.assertEqual(scoped_glob["warnings"][0]["code"], "reparse_point_skipped")
        self.assertEqual(scoped_grep["warnings"][0]["code"], "reparse_point_skipped")
        self.assertTrue(any(item["code"] == "reparse_point_skipped" for item in glob_result["warnings"]))
        self.assertTrue(any(item["code"] == "reparse_point_skipped" for item in grep_result["warnings"]))

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_windows_junction_is_rejected_for_writes(self):
        target = self._path("junction-target")
        junction = self._path("junction")
        os.makedirs(target)
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", junction, target],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            self.skipTest(f"Junction creation failed: {completed.stderr or completed.stdout}")
        try:
            glob_result = glob_paths(self.workspace, pattern="*.txt")
            grep_result = grep_contents(self.workspace, pattern="value")
            self.assertTrue(any(item["code"] == "reparse_point_skipped" for item in glob_result["warnings"]))
            self.assertTrue(any(item["code"] == "reparse_point_skipped" for item in grep_result["warnings"]))
            result = apply_patch(
                self.workspace,
                "*** Begin Patch\n*** Add File: junction/blocked.txt\n+value\n*** End Patch",
                context=self.context,
            )
            self.assertEqual(result["error"]["code"], "reparse_point_not_allowed")
        finally:
            if os.path.lexists(junction):
                os.rmdir(junction)

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_windows_junction_workspace_root_is_rejected_for_writes(self):
        container = tempfile.mkdtemp()
        target = os.path.join(container, "real-workspace")
        junction = os.path.join(container, "workspace-junction")
        os.makedirs(target)
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", junction, target],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            shutil.rmtree(container, ignore_errors=True)
            self.skipTest(f"Junction creation failed: {completed.stderr or completed.stdout}")
        try:
            result = apply_patch(
                junction,
                "*** Begin Patch\n*** Add File: blocked.txt\n+value\n*** End Patch",
                context={},
            )
            self.assertEqual(result["error"]["code"], "reparse_point_not_allowed")
            self.assertFalse(os.path.exists(os.path.join(target, "blocked.txt")))
        finally:
            if os.path.lexists(junction):
                os.rmdir(junction)
            shutil.rmtree(container, ignore_errors=True)

    def test_skill_exports_only_the_converged_text_tools(self):
        module = _load_file_skill_module()
        exports = {item["name"]: item for item in module.TOOL_EXPORTS}
        self.assertIn("text_file_read", exports)
        self.assertIn("apply_patch", exports)
        self.assertEqual(exports["apply_patch"]["parameters"]["required"], ["patch"])
        self.assertEqual(
            exports["apply_patch"]["parameters"]["properties"]["patch"]["type"],
            "string",
        )
        self.assertNotIn("text_file_" + "write", exports)
        self.assertNotIn("text_file_" + "update", exports)

    def test_removed_tool_names_do_not_leak_into_runtime_prompt_skill_or_ui_sources(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        source_paths = (
            os.path.join(repo_root, "core", "agent.py"),
            os.path.join(repo_root, "core", "tool_registry.py"),
            os.path.join(repo_root, "skills", "file-system", "impl.py"),
            os.path.join(repo_root, "skills", "file-system", "SKILL.md"),
            os.path.join(repo_root, "main.py"),
        )
        removed_names = ("text_file_" + "write", "text_file_" + "update")
        for source_path in source_paths:
            with self.subTest(source_path=source_path), open(source_path, "r", encoding="utf-8") as handle:
                source = handle.read()
                for removed_name in removed_names:
                    self.assertNotIn(removed_name, source)

    def test_apply_patch_uses_standard_function_schema_across_providers(self):
        module = _load_file_skill_module()
        export = next(item for item in module.TOOL_EXPORTS if item["name"] == "apply_patch")
        definition = {
            "type": "function",
            "function": {
                "name": export["name"],
                "description": export["description"],
                "parameters": export["parameters"],
            },
        }

        for model_name, base_url in (
            ("deepseek-chat", "https://api.deepseek.com"),
            ("compatible-model", "https://example.com/v1"),
        ):
            provider = OpenAIProvider.__new__(OpenAIProvider)
            provider.api_protocol = API_PROTOCOL_CHAT_COMPLETIONS
            provider.model_name = model_name
            provider.base_url = base_url
            provider.thinking_enabled = False
            provider.reasoning_effort = None
            provider.stream_usage_enabled = False
            provider.prompt_cache_key_param = ""
            provider.supports_vision = False
            provider.client = MagicMock()
            provider.client.chat.completions.create.return_value = []

            list(provider.chat_stream([{"role": "user", "content": "test"}], tools=[definition]))
            sent = provider.client.chat.completions.create.call_args.kwargs["tools"]
            self.assertEqual(sent[0]["function"]["parameters"], export["parameters"])

        anthropic = AnthropicProvider.__new__(AnthropicProvider)
        converted = anthropic._convert_tools([definition])
        self.assertEqual(converted[0]["name"], "apply_patch")
        self.assertEqual(converted[0]["input_schema"], export["parameters"])


if __name__ == "__main__":
    unittest.main()
