import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.audio_attachments import (
    is_audio_attachment,
    partition_model_visible_attachments,
)
from core.llm.providers import _build_openai_user_content
from main import MainWindow


class TestAudioAttachmentPrivacy(unittest.TestCase):
    def test_partition_only_changes_audio_when_local_mode_is_enabled(self):
        files = [r"C:\workspace\meeting.m4a", r"C:\workspace\notes.txt"]
        visible, local = partition_model_visible_attachments(files, keep_audio_local=True)
        self.assertEqual(visible, [files[1]])
        self.assertEqual(local, [files[0]])
        self.assertTrue(is_audio_attachment(files[0]))

        unchanged, local = partition_model_visible_attachments(files, keep_audio_local=False)
        self.assertEqual(unchanged, files)
        self.assertEqual(local, [])

    def test_selected_speech_skill_keeps_audio_out_of_provider_projection(self):
        with tempfile.TemporaryDirectory() as workspace:
            audio = os.path.join(workspace, "private-meeting.m4a")
            notes = os.path.join(workspace, "notes.txt")
            Path(audio).write_bytes(b"private-audio")
            Path(notes).write_text("public notes", encoding="utf-8")

            window = MainWindow.__new__(MainWindow)
            window.workspace_dir = workspace
            window._workspace_dir_for_state = lambda: workspace
            payload = window._build_user_message_payload(
                "请转成文字",
                [audio, notes],
                keep_audio_local=True,
            )

            self.assertNotIn("private-meeting.m4a", payload["content"])
            self.assertNotIn(audio, payload["content"])
            self.assertIn(notes, payload["content"])
            self.assertEqual(payload["meta"]["local_only_audio_files"], [audio])
            projected = _build_openai_user_content(
                payload["content"],
                payload["content_parts"],
                supports_vision=False,
            )
            serialized = json.dumps(projected, ensure_ascii=False)
            self.assertNotIn("private-meeting.m4a", serialized)
            self.assertNotIn(audio, serialized)
            self.assertTrue(
                any(notes in str(part.get("text") or "") for part in projected),
                projected,
            )

    def test_unselected_speech_skill_preserves_existing_attachment_behavior(self):
        with tempfile.TemporaryDirectory() as workspace:
            audio = os.path.join(workspace, "meeting.m4a")
            Path(audio).write_bytes(b"audio")
            window = MainWindow.__new__(MainWindow)
            window.workspace_dir = workspace
            window._workspace_dir_for_state = lambda: workspace

            payload = window._build_user_message_payload(
                "分析附件",
                [audio],
                keep_audio_local=False,
            )

            self.assertIn(audio, payload["content"])
            self.assertEqual(payload["content_parts"][1]["path"], audio)
            self.assertNotIn("local_only_audio_files", payload["meta"])

    def test_missing_speech_component_blocks_submit_before_ai_and_links_settings(self):
        window = MainWindow.__new__(MainWindow)
        window._show_conversation_notice = MagicMock()
        window.open_settings = MagicMock()
        state = type("_Session", (), {"session_id": "session-1"})()

        with patch(
            "main.speech_to_text_component_status",
            return_value={
                "ready": False,
                "needs_update": False,
                "needs_repair": False,
                "health_error": "语音转文字组件尚未安装。",
            },
        ):
            allowed = window._ensure_speech_component_before_submit(
                state,
                [r"C:\workspace\private.m4a"],
                keep_audio_local=True,
            )

        self.assertFalse(allowed)
        notice_args = window._show_conversation_notice.call_args
        self.assertIn("尚未提交给 AI", notice_args.args[1])
        self.assertEqual(notice_args.kwargs["action_text"], "打开组件与依赖")
        notice_args.kwargs["action_callback"]()
        window.open_settings.assert_called_once_with("组件与依赖")

    def test_ready_speech_component_allows_submit_without_notice(self):
        window = MainWindow.__new__(MainWindow)
        window._show_conversation_notice = MagicMock()
        state = type("_Session", (), {"session_id": "session-1"})()

        with patch("main.speech_to_text_component_status", return_value={"ready": True}):
            allowed = window._ensure_speech_component_before_submit(
                state,
                [r"C:\workspace\private.webm"],
                keep_audio_local=True,
            )

        self.assertTrue(allowed)
        window._show_conversation_notice.assert_not_called()

    def test_remote_speech_backend_skips_component_and_uses_remote_privacy_placeholder(self):
        with tempfile.TemporaryDirectory() as workspace:
            audio = os.path.join(workspace, "private.webm")
            Path(audio).write_bytes(b"audio")
            window = MainWindow.__new__(MainWindow)
            window.workspace_dir = workspace
            window._show_conversation_notice = MagicMock()
            state = type("_Session", (), {"session_id": "session-remote"})()
            config = {
                "backend": "openai_compatible",
                "api_url": "http://asr.internal/sync/v1/audio/transcriptions",
                "model_name": "Qwen3-ASR-1.7B",
                "api_key": "secret",
            }

            with patch("main.speech_to_text_component_status") as component_status:
                allowed = window._ensure_speech_component_before_submit(
                    state,
                    [audio],
                    keep_audio_local=True,
                    transcription_config=config,
                )

            self.assertTrue(allowed)
            component_status.assert_not_called()
            payload = window._build_user_message_payload(
                "请转录",
                [audio],
                keep_audio_local=True,
                audio_transcription_backend="openai_compatible",
            )
            self.assertIn("已配置的语音转文字服务", payload["content"])
            self.assertNotIn("private.webm", payload["content"])
            self.assertEqual(payload["meta"]["transcription_only_audio_files"], [audio])


if __name__ == "__main__":
    unittest.main()
