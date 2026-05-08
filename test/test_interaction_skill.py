import importlib.util
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skills.interaction import impl as interaction_impl


class TestInteractionSkill(unittest.TestCase):
    def test_request_user_approval_returns_structured_payload(self):
        with patch.object(
            interaction_impl.interaction_service,
            "create_request",
            return_value={
                "request_id": "req-1",
                "status": "completed",
                "approved": True,
                "text": "",
                "selected_options": [],
                "raw_value": True,
                "resolved_at": "2026-04-12T00:00:00+00:00",
            },
        ) as create_mock:
            result = interaction_impl.request_user_approval(
                "Delete this file?",
                severity="high",
                details="path=/tmp/demo.txt",
                _context={"session_id": "session-1"},
            )

        self.assertEqual(result["source_tool"], "request_user_approval")
        self.assertTrue(result["interaction_response"]["approved"])
        self.assertEqual(result["interaction_request"]["severity"], "high")
        create_mock.assert_called_once()

    def test_request_user_input_choice_returns_structured_selection(self):
        with patch.object(
            interaction_impl.interaction_service,
            "create_request",
            return_value={
                "request_id": "req-2",
                "status": "completed",
                "approved": True,
                "text": "alpha",
                "selected_options": ["alpha"],
                "raw_value": "1",
                "resolved_at": "2026-04-12T00:00:00+00:00",
            },
        ):
            result = interaction_impl.request_user_input(
                "Pick one",
                input_mode="choice",
                options=[
                    {"label": "Alpha", "value": "alpha"},
                    {"label": "Beta", "value": "beta"},
                ],
                _context={"session_id": "session-2"},
            )

        self.assertEqual(result["source_tool"], "request_user_input")
        self.assertEqual(result["interaction_request"]["kind"], "choice")
        self.assertEqual(result["interaction_response"]["selected_options"], ["alpha"])
        self.assertIn("alpha", result["content"])

    def test_request_user_input_questionnaire_returns_answers(self):
        with patch.object(
            interaction_impl.interaction_service,
            "create_request",
            return_value={
                "request_id": "req-3",
                "status": "completed",
                "approved": True,
                "text": "",
                "selected_options": [],
                "answers": {
                    "compatibility_target": {
                        "selected_options": ["彻底重做"],
                        "text": "",
                        "raw_value": "彻底重做",
                    }
                },
                "raw_value": {"compatibility_target": "彻底重做"},
                "resolved_at": "2026-04-12T00:00:00+00:00",
            },
        ):
            result = interaction_impl.request_user_input(
                message="请选择方案",
                questions=[
                    {
                        "header": "兼容策略",
                        "id": "compatibility_target",
                        "question": "是否保留旧计划结构？",
                        "options": [{"label": "彻底重做", "description": "移除旧结构"}],
                    }
                ],
                _context={"session_id": "session-3"},
            )

        self.assertEqual(result["interaction_request"]["kind"], "questionnaire")
        self.assertIn("compatibility_target", result["answers"])
        self.assertTrue(result["interaction_response"]["approved"])

    def test_publish_artifacts_feishu_context_returns_structured_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            file_path = os.path.join(tmp, "sample.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("demo")

            result = interaction_impl.publish_artifacts(
                items=[{"path": file_path, "caption": "demo file"}],
                audience="feishu",
                summary="artifact ready",
                _context={
                    "run_context": {"im_provider": "feishu", "channel": "feishu"},
                    "im_event": {"chat_id": "chat-1"},
                    "config_manager": type(
                        "Cfg",
                        (),
                        {
                            "get": lambda self, key, default=None: {
                                "providers": {
                                    "feishu": {
                                        "app_id": "",
                                        "app_secret": "",
                                    }
                                }
                            }
                            if key == "im_gateway"
                            else default
                        },
                    )(),
                },
            )

        self.assertEqual(result["source_tool"], "publish_artifacts")
        parts = result.get("content_parts") or []
        file_parts = [p for p in parts if isinstance(p, dict) and p.get("type") == "file"]
        self.assertTrue(file_parts)
        self.assertEqual(file_parts[0].get("artifact_source"), "publish_artifacts")
        self.assertIn("feishu", result["delivery_result"])

    def test_publish_artifacts_missing_file_returns_error(self):
        result = interaction_impl.publish_artifacts(
            items=[{"path": "Z:/not-found.bin"}],
            audience="feishu",
            _context={"run_context": {"im_provider": "feishu"}},
        )
        self.assertIn("file not found", (result.get("error") or "").lower())

    def test_publish_artifacts_requires_enterprise_context(self):
        result = interaction_impl.publish_artifacts(
            items=[{"url": "https://example.com/demo.txt", "name": "demo.txt"}],
            audience="feishu",
        )
        self.assertIn("only available in enterprise", (result.get("error") or "").lower())

    def test_publish_artifacts_supports_dingtalk_context_with_fallback(self):
        result = interaction_impl.publish_artifacts(
            items=[{"url": "https://example.com/demo.txt", "name": "demo.txt"}],
            audience="dingtalk",
            _context={
                "run_context": {"im_provider": "dingtalk", "channel": "dingtalk"},
                "config_manager": type(
                    "Cfg",
                    (),
                    {
                        "get": lambda self, key, default=None: {
                            "providers": {
                                "dingtalk": {
                                    "webhook_url": "",
                                }
                            }
                        }
                        if key == "im_gateway"
                        else default
                    },
                )(),
            },
        )
        self.assertIn("dingtalk", result["delivery_result"])
        self.assertFalse(result["delivery_result"]["dingtalk"]["enabled"])

    def test_publish_artifacts_supports_wecom_context_with_fallback(self):
        result = interaction_impl.publish_artifacts(
            items=[{"url": "https://example.com/demo.txt", "name": "demo.txt"}],
            audience="wecom",
            _context={
                "run_context": {"im_provider": "wecom", "channel": "wecom"},
                "config_manager": type(
                    "Cfg",
                    (),
                    {
                        "get": lambda self, key, default=None: {
                            "providers": {
                                "wecom": {
                                    "webhook_url": "",
                                }
                            }
                        }
                        if key == "im_gateway"
                        else default
                    },
                )(),
            },
        )
        self.assertIn("wecom", result["delivery_result"])
        self.assertFalse(result["delivery_result"]["wecom"]["enabled"])

    def test_delete_file_requires_request_user_approval(self):
        module_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "skills",
            "file-system",
            "impl.py",
        )
        spec = importlib.util.spec_from_file_location("file_system_impl_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as tmp:
            file_path = os.path.join(tmp, "delete-me.txt")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("demo")

            with patch.object(
                module,
                "request_user_approval",
                return_value={
                    "interaction_response": {
                        "approved": False,
                        "status": "completed",
                    }
                },
            ) as approval_mock:
                result = module.delete_file(tmp, "delete-me.txt", _context={"session_id": "session-delete"})

        self.assertIn("cancelled", result.lower())
        approval_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
