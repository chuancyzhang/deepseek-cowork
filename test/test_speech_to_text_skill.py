import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.skill_manager import SkillManager


REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = REPO_ROOT / "ai_skills" / "speech-to-text"


def load_speech_module(module_name):
    module_path = SKILL_ROOT / "impl.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigStub:
    def is_skill_enabled(self, _skill_name, default_enabled=True):
        return default_enabled

    def get_mcp_servers(self):
        return []

    def get(self, _key, default=None):
        return default

    def get_skill_config(self, _skill_name):
        return {}


class SignalStub:
    def __init__(self):
        self.values = []

    def emit(self, value):
        self.values.append(value)


class TestSpeechToTextSkill(unittest.TestCase):
    def setUp(self):
        self.workspace_dir = tempfile.mkdtemp()
        self.audio_path = os.path.join(self.workspace_dir, "meeting.wav")
        Path(self.audio_path).write_bytes(b"test-audio-placeholder")
        self.module = load_speech_module(f"speech_to_text_impl_{id(self)}")
        self.payload = {
            "ok": True,
            "transcript": (
                "[00:00.10–00:01.00] Speaker 1: 大家好。\n\n"
                "[00:01.10–00:02.00] Speaker 2: 你好。"
            ),
            "model": "sensevoice",
            "lang": "zh",
            "emotion": "",
            "event": "",
            "duration": 2.0,
            "diarized": True,
            "speaker_count": 2,
        }

    def tearDown(self):
        shutil.rmtree(self.workspace_dir, ignore_errors=True)

    def _run_transcription(self, *, polish, stdout_payload=None, context=None, audio_path="meeting.wav"):
        runner_result = {
            "ok": True,
            "exit_code": 0,
            "stdout": json.dumps(stdout_payload or self.payload, ensure_ascii=False),
            "stderr": "",
        }
        with patch.object(
            self.module,
            "_require_component",
            return_value={
                "sensevoice_model": "sensevoice.onnx",
                "sensevoice_tokens": "tokens.txt",
                "segmentation": "segmentation.onnx",
                "embedding": "embedding.onnx",
            },
        ), patch.object(
            self.module,
            "run_skill_script_in_sandbox",
            return_value=runner_result,
        ) as runner:
            result = json.loads(self.module.transcribe_audio(
                audio_path,
                polish,
                workspace_dir=self.workspace_dir,
                _context=context or {},
            ))
        return result, runner

    def test_manifest_registers_default_enabled_local_dependencies_and_two_tools(self):
        manifest = json.loads((SKILL_ROOT / "skill.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["default_enabled"])
        self.assertEqual(manifest["source_type"], "bundled_plugin")
        self.assertEqual(
            manifest["node_dependencies"],
            [],
        )
        self.assertEqual(manifest["tool_refs"], ["transcribe_audio", "save_transcript_result"])

        manager = SkillManager(
            workspace_dir=self.workspace_dir,
            config_manager=ConfigStub(),
            auto_load=False,
            load_mcp_tools=False,
        )
        manager.skills_dirs = [str(REPO_ROOT / "ai_skills")]
        manager.load_skills(load_mcp_tools=False)
        validation = manager.validate_skill("speech-to-text")
        self.assertTrue(validation["ok"], validation["issues"])
        self.assertIn("transcribe_audio", manager.tools)
        self.assertIn("save_transcript_result", manager.tools)

    def test_local_only_writes_speaker_transcript_without_returning_body_to_model(self):
        observability = SignalStub()
        result, runner = self._run_transcription(
            polish=False,
            context={"observability_signal": observability},
        )

        self.assertTrue(result["ok"])
        self.assertNotIn("transcript", result)
        self.assertNotIn("raw_text", result)
        self.assertEqual(result["speaker_count"], 2)
        self.assertIn("正文未返回给当前大模型", result["privacy_notice"])
        output = Path(result["output_path"])
        self.assertTrue(output.is_file())
        content = output.read_text(encoding="utf-8")
        self.assertIn("privacy: \"local-only\"", content)
        self.assertIn("Speaker 1: 大家好。", content)
        self.assertIn("Speaker 2: 你好。", content)
        self.assertEqual(
            [event["status"] for event in observability.values],
            ["submit", "start", "run", "finish"],
        )
        self.assertIn("--speaker-count", runner.call_args.kwargs["args"])

    def test_ai_polish_returns_text_then_saves_polished_result(self):
        result, _runner = self._run_transcription(polish=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["transcript"], self.payload["transcript"])
        self.assertTrue(Path(result["output_path"]).is_file())
        self.assertTrue(Path(result["raw_transcript_path"]).is_file())
        fallback_frontmatter, fallback_body = self.module._read_markdown_parts(
            result["suggested_output_path"]
        )
        self.assertIn('privacy: "local-raw-fallback"', fallback_frontmatter)
        self.assertEqual(fallback_body, self.payload["transcript"])

        observability = SignalStub()
        saved = json.loads(self.module.save_transcript_result(
            result["raw_transcript_path"],
            True,
            polished_text=(
                "[00:00.10–00:01.00] Speaker 1: 大家好！\n\n"
                "[00:01.10–00:02.00] Speaker 2: 你好。"
            ),
            workspace_dir=self.workspace_dir,
            _context={"observability_signal": observability},
        ))
        self.assertTrue(saved["ok"])
        final = Path(saved["output_path"]).read_text(encoding="utf-8")
        self.assertIn("ai_polished: true", final)
        self.assertIn("privacy: \"ai-polished\"", final)
        self.assertIn("大家好！", final)
        self.assertEqual(
            [event["status"] for event in observability.values],
            ["submit", "start", "run", "finish"],
        )

    def test_polish_failure_promotes_exact_raw_body(self):
        result, _runner = self._run_transcription(polish=True)
        raw_frontmatter, raw_body = self.module._read_markdown_parts(result["raw_transcript_path"])
        self.assertIn("ai_polished: false", raw_frontmatter)

        saved = json.loads(self.module.save_transcript_result(
            result["raw_transcript_path"],
            False,
            polished_text="这段内容必须被忽略",
            workspace_dir=self.workspace_dir,
        ))
        self.assertTrue(saved["ok"])
        final_frontmatter, final_body = self.module._read_markdown_parts(saved["output_path"])
        self.assertEqual(final_body, raw_body)
        self.assertIn("ai_polished: false", final_frontmatter)
        self.assertIn("privacy: \"local-raw-fallback\"", final_frontmatter)

    def test_diarization_failure_is_explicit_and_does_not_write_single_speaker_fallback(self):
        failed_payload = {
            "ok": False,
            "error": {
                "code": "local_transcription_failed",
                "message": "Speaker diarization returned no segments.",
            },
        }
        with patch.object(
            self.module,
            "_require_component",
            return_value={
                "sensevoice_model": "sensevoice.onnx",
                "sensevoice_tokens": "tokens.txt",
                "segmentation": "segmentation.onnx",
                "embedding": "embedding.onnx",
            },
        ), patch.object(
            self.module,
            "run_skill_script_in_sandbox",
            return_value={
                "ok": False,
                "exit_code": 1,
                "stdout": json.dumps(failed_payload),
                "stderr": "",
            },
        ):
            result = json.loads(self.module.transcribe_audio(
                "meeting.wav",
                False,
                workspace_dir=self.workspace_dir,
            ))

        self.assertFalse(result["ok"])
        self.assertIn("Speaker diarization returned no segments", result["error"]["message"])
        self.assertFalse(Path(self.workspace_dir, "meeting-transcript.md").exists())

    def test_outside_workspace_requires_explicit_attachment_authorization(self):
        outside_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside_dir, True)
        outside_audio = os.path.join(outside_dir, "attached.wav")
        Path(outside_audio).write_bytes(b"attached-audio")

        with patch.object(
            self.module,
            "_require_component",
            return_value={
                "sensevoice_model": "sensevoice.onnx",
                "sensevoice_tokens": "tokens.txt",
                "segmentation": "segmentation.onnx",
                "embedding": "embedding.onnx",
            },
        ):
            rejected = json.loads(self.module.transcribe_audio(
                outside_audio,
                False,
                workspace_dir=self.workspace_dir,
            ))
        self.assertFalse(rejected["ok"])
        self.assertIn("本会话中用户明确附加", rejected["error"]["message"])

        context = {
            "current_messages_snapshot": [{
                "role": "user",
                "meta": {"user_added_files": [outside_audio]},
            }]
        }
        accepted, _runner = self._run_transcription(
            polish=False,
            context=context,
            audio_path=outside_audio,
        )
        self.assertTrue(accepted["ok"])

    def test_missing_component_fails_before_local_script_and_points_to_settings(self):
        with patch.object(
            self.module,
            "speech_to_text_component_status",
            return_value={"ready": False, "health_error": "语音组件尚未安装。"},
        ), patch.object(self.module, "run_skill_script_in_sandbox") as runner:
            result = json.loads(self.module.transcribe_audio(
                "meeting.wav",
                False,
                workspace_dir=self.workspace_dir,
            ))

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "component_not_ready")
        self.assertIn("设置 → 组件与依赖", result["error"]["message"])
        runner.assert_not_called()

    def test_omitted_audio_path_uses_single_current_attachment_without_exposing_name(self):
        context = {
            "current_messages_snapshot": [{
                "role": "user",
                "meta": {"user_added_files": [self.audio_path]},
            }]
        }
        result, runner = self._run_transcription(
            polish=False,
            context=context,
            audio_path="",
        )

        self.assertTrue(result["ok"])
        self.assertNotIn("meeting", result["output_path"])
        args = runner.call_args.kwargs["args"]
        self.assertEqual(args[args.index("--input") + 1], self.audio_path)

    def test_node_alignment_self_test(self):
        candidates = [
            REPO_ROOT / "node_env" / "node.exe",
            Path(shutil.which("node") or ""),
        ]
        node = next((path for path in candidates if str(path) and path.is_file()), None)
        if node is None:
            self.skipTest("Node runtime is unavailable")
        completed = subprocess.run(
            [str(node), str(SKILL_ROOT / "scripts" / "transcribe.mjs"), "--self-test"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual([turn["speaker"] for turn in payload["turns"]], ["Speaker 1", "Speaker 2"])


if __name__ == "__main__":
    unittest.main()
