import os
import sys
import asyncio
import threading
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import im_gateway
from core.im_gateway import runtime as im_gateway_runtime
from core.im_gateway_config import (
    disable_im_gateway,
    normalize_im_gateway_config,
    update_selected_provider,
)
from core.im_gateway_registration import FEISHU_ADDONS, register_feishu_app
from core.im_gateway_status import read_im_gateway_status, write_im_gateway_status


class _ProviderStub:
    def __init__(self, event):
        self._event = event
        self.replies = []

    def parse_event(self, payload):
        return dict(self._event)

    def send_card_reply(self, event, card_content="", title="", **kwargs):
        self.replies.append(
            {
                "event": dict(event or {}),
                "card_content": card_content,
                "title": title,
                "kwargs": kwargs,
            }
        )
        return "msg-1"


class _SessionMapperStub:
    def get_or_create(self, provider, session_key):
        return "conversation-1"


class _ConfigStub:
    def __init__(self, im_gateway=None):
        self.im_gateway = im_gateway or {}

    def load_config(self):
        return None

    def get(self, key, default=None):
        if key == "default_workspace":
            return "D:\\code\\cowork"
        if key == "im_gateway":
            return self.im_gateway
        return default

    def get_god_mode(self):
        return False

    def set(self, key, value):
        return None


class _DaemonClientStub:
    def __init__(self, pending):
        self.pending = pending
        self.respond_calls = []

    def get_pending_interaction(self, session_id):
        return {"status": "ok", "pending": self.pending}

    def respond_interaction(self, request_id, result):
        self.respond_calls.append({"request_id": request_id, "result": result})
        return {"status": "ok", "resolved": True}


class _StreamDaemonClientStub:
    def __init__(self):
        self.calls = []

    def send_message_stream(self, session_id, content, workspace_dir=None, run_context=None):
        self.calls.append(
            {
                "session_id": session_id,
                "content": content,
                "workspace_dir": workspace_dir,
                "run_context": dict(run_context or {}),
            }
        )
        yield {"type": "final", "result": {"content": "done"}}


class TestImGatewayPendingInteraction(unittest.TestCase):
    def test_gateway_status_redacts_urls_and_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.im_gateway_status.get_app_data_dir",
            return_value=temp_dir,
        ):
            write_im_gateway_status(
                "wecom",
                "error",
                "secret=abc token:xyz https://example.test/path?code=private",
            )
            status = read_im_gateway_status()
        self.assertEqual(status["provider"], "wecom")
        self.assertEqual(status["state"], "error")
        self.assertNotIn("abc", status["error"])
        self.assertNotIn("xyz", status["error"])
        self.assertNotIn("example.test", status["error"])

    def test_feishu_registration_uses_qr_flow_and_bot_addons(self):
        captured = {}

        def fake_register_app(**kwargs):
            captured.update(kwargs)
            kwargs["on_qr_code"]({"url": "https://example.test/qr", "expire_in": 60})
            return {"client_id": "cli-created", "client_secret": "created-secret"}

        qr_events = []
        fake_lark = SimpleNamespace(register_app=fake_register_app)
        with patch.dict(sys.modules, {"lark_oapi": fake_lark}):
            result = register_feishu_app(
                on_qr_code=lambda info: qr_events.append(info),
                existing_app_id="",
            )
        self.assertEqual(result["app_id"], "cli-created")
        self.assertTrue(captured["create_only"])
        self.assertEqual(captured["addons"], FEISHU_ADDONS)
        self.assertEqual(qr_events[0]["expire_in"], 60)

    def test_feishu_registration_updates_existing_app(self):
        captured = {}

        def fake_register_app(**kwargs):
            captured.update(kwargs)
            return {"client_id": "cli-existing", "client_secret": "updated-secret"}

        with patch.dict(
            sys.modules,
            {"lark_oapi": SimpleNamespace(register_app=fake_register_app)},
        ):
            register_feishu_app(lambda _info: None, existing_app_id="cli-existing")
        self.assertEqual(captured["app_id"], "cli-existing")
        self.assertNotIn("create_only", captured)

    def test_pending_choice_reply_is_consumed_before_model_roundtrip(self):
        provider = _ProviderStub(
            {
                "event_type": "im.message.receive_v1",
                "sender_type": "user",
                "message_type": "text",
                "text": "1",
                "user_id": "user-1",
                "chat_id": "chat-1",
                "message_id": "msg-1",
                "create_time": "1710000000000",
            }
        )
        daemon_client = _DaemonClientStub(
            {
                "request_id": "req-choice",
                "session_id": "conversation-1",
                "kind": "choice",
                "title": "需要你的输入",
                "message": "请选择一个方案",
                "options": [
                    {"label": "Alpha", "value": "alpha"},
                    {"label": "Beta", "value": "beta"},
                ],
                "allow_free_text": False,
                "timeout_seconds": 120,
            }
        )

        result = im_gateway._handle_im_event(
            payload={"ignored": True},
            provider=provider,
            session_mapper=_SessionMapperStub(),
            config_manager=_ConfigStub(),
            daemon_client=daemon_client,
        )

        self.assertIsNone(result)
        self.assertEqual(len(daemon_client.respond_calls), 1)
        self.assertEqual(daemon_client.respond_calls[0]["request_id"], "req-choice")
        self.assertEqual(
            daemon_client.respond_calls[0]["result"]["selected_options"],
            ["alpha"],
        )
        self.assertIn("已收到选择", provider.replies[-1]["card_content"])

    def test_invalid_pending_reply_returns_hint_without_resolving(self):
        provider = _ProviderStub(
            {
                "event_type": "im.message.receive_v1",
                "sender_type": "user",
                "message_type": "text",
                "text": "unknown",
                "user_id": "user-1",
                "chat_id": "chat-1",
                "message_id": "msg-2",
                "create_time": "1710000000000",
            }
        )
        daemon_client = _DaemonClientStub(
            {
                "request_id": "req-approval",
                "session_id": "conversation-1",
                "kind": "approval",
                "title": "请确认",
                "message": "是否继续？",
                "options": [],
                "allow_free_text": False,
                "timeout_seconds": 120,
            }
        )

        result = im_gateway._handle_im_event(
            payload={"ignored": True},
            provider=provider,
            session_mapper=_SessionMapperStub(),
            config_manager=_ConfigStub(),
            daemon_client=daemon_client,
        )

        self.assertIsNone(result)
        self.assertEqual(daemon_client.respond_calls, [])
        self.assertIn("请回复：是 / 否", provider.replies[-1]["card_content"])

    def test_stream_im_response_passes_feishu_run_context(self):
        provider = _ProviderStub(
            {
                "event_type": "im.message.receive_v1",
                "sender_type": "user",
                "message_type": "text",
                "text": "send artifact",
                "user_id": "user-1",
                "chat_id": "chat-1",
                "message_id": "msg-3",
                "create_time": "1710000000000",
            }
        )
        daemon_client = _StreamDaemonClientStub()

        result = im_gateway._stream_im_response(
            "conversation-1",
            provider._event,
            provider,
            daemon_client,
            "D:\\code\\cowork",
            config_manager=_ConfigStub(),
        )

        self.assertIsInstance(result, tuple)
        self.assertEqual(len(daemon_client.calls), 1)
        run_context = daemon_client.calls[0]["run_context"]
        self.assertEqual(run_context.get("im_provider"), "feishu")
        self.assertEqual(run_context.get("channel"), "feishu")

    def test_enabled_provider_names_normalizes_to_one_channel(self):
        cfg = _ConfigStub(
            {
                "enabled_providers": ["feishu", "dingtalk"],
                "providers": {
                    "feishu": {"enabled": True},
                    "dingtalk": {"enabled": True},
                    "wecom": {"enabled": False},
                },
            }
        )

        self.assertEqual(
            im_gateway._enabled_provider_names(cfg),
            ["feishu"],
        )

    def test_single_provider_config_preserves_inactive_credentials(self):
        source = {
            "enabled_providers": ["feishu", "wecom"],
            "providers": {
                "feishu": {"enabled": True, "app_id": "cli-a", "app_secret": "secret-a"},
                "wecom": {"enabled": True, "bot_id": "bot-a", "secret": "secret-b"},
            },
        }
        normalized = normalize_im_gateway_config(source)
        self.assertEqual(normalized["enabled_providers"], ["feishu"])
        self.assertFalse(normalized["providers"]["wecom"]["enabled"])
        switched = update_selected_provider(
            normalized,
            "wecom",
            {"bot_id": "bot-b", "secret": "secret-c"},
        )
        self.assertEqual(switched["enabled_providers"], ["wecom"])
        self.assertEqual(switched["providers"]["feishu"]["app_id"], "cli-a")
        self.assertFalse(switched["providers"]["feishu"]["enabled"])
        disabled = disable_im_gateway(switched)
        self.assertEqual(disabled["enabled_providers"], [])
        self.assertEqual(disabled["providers"]["wecom"]["bot_id"], "bot-b")

    def test_dingtalk_event_parse_and_run_context(self):
        provider = im_gateway.DingTalkProvider(
            _ConfigStub(
                {
                    "providers": {
                        "dingtalk": {
                            "enabled": True,
                            "webhook_url": "",
                        }
                    }
                }
            )
        )
        payload = {
            "text": {"content": "hello"},
            "senderStaffId": "user-1",
            "conversationId": "chat-1",
            "msgId": "dt-msg-1",
            "createAt": "1710000000000",
        }
        event = provider.parse_event(payload)
        self.assertEqual(event["provider"], "dingtalk")
        self.assertEqual(event["text"], "hello")

        daemon_client = _StreamDaemonClientStub()
        result = im_gateway._stream_im_response(
            "conversation-dt",
            event,
            provider,
            daemon_client,
            "D:\\code\\cowork",
            config_manager=_ConfigStub(),
        )

        self.assertIsInstance(result, tuple)
        run_context = daemon_client.calls[0]["run_context"]
        self.assertEqual(run_context.get("im_provider"), "dingtalk")
        self.assertEqual(run_context.get("channel"), "dingtalk")

    def test_wecom_event_parse(self):
        provider = im_gateway.WeComProvider(
            _ConfigStub(
                {
                    "providers": {
                        "wecom": {
                            "enabled": True,
                            "webhook_url": "",
                        }
                    }
                }
            )
        )
        event = provider.parse_event(
            {
                "text": {"content": "hello"},
                "from_user_id": "user-2",
                "chat_id": "room-1",
                "msgid": "wx-msg-1",
                "timestamp": "1710000000000",
            }
        )
        self.assertEqual(event["provider"], "wecom")
        self.assertEqual(event["user_id"], "user-2")
        self.assertEqual(event["chat_id"], "room-1")

    def test_wecom_sdk_frame_parse(self):
        provider = im_gateway.WeComProvider(
            _ConfigStub(
                {
                    "providers": {
                        "wecom": {
                            "enabled": True,
                            "bot_id": "bot-1",
                            "secret": "secret-1",
                        }
                    }
                }
            )
        )
        frame = {
            "headers": {"req_id": "req-1"},
            "body": {
                "msgid": "wx-msg-2",
                "msgtype": "text",
                "chatid": "room-2",
                "from": {"userid": "user-3"},
                "text": {"content": "from sdk"},
            },
        }
        event = provider.parse_event(frame)
        self.assertEqual(event["event_type"], "message")
        self.assertEqual(event["text"], "from sdk")
        self.assertEqual(event["user_id"], "user-3")
        self.assertEqual(event["chat_id"], "room-2")
        self.assertIs(event["sdk_frame"], frame)

    def test_wecom_stream_reply_bridge_uses_sdk_loop(self):
        provider = im_gateway.WeComProvider(
            _ConfigStub(
                {
                    "providers": {
                        "wecom": {
                            "enabled": True,
                            "bot_id": "bot-1",
                            "secret": "secret-1",
                        }
                    }
                }
            )
        )

        class _Client:
            def __init__(self):
                self.calls = []

            async def reply_stream(self, frame, stream_id, content, finish):
                self.calls.append((frame, stream_id, content, finish))
                return {"ok": True}

        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever)
        loop_thread.start()
        client = _Client()
        provider.attach_client(client, loop, lambda prefix: f"{prefix}-1")
        frame = {"headers": {"req_id": "req-1"}, "body": {"text": {"content": "hello"}}}
        try:
            stream_id = provider.send_card_reply(
                {"sdk_frame": frame},
                card_content="处理中",
                streaming=True,
            )
            self.assertEqual(stream_id, "stream-1")
            self.assertTrue(
                provider.update_card_message(
                    stream_id,
                    "完成",
                    collapse_thinking=True,
                )
            )
            self.assertEqual(
                [(item[2], item[3]) for item in client.calls],
                [("处理中", False), ("完成", True)],
            )
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            loop.close()

    def test_wecom_stream_reply_failure_is_exposed(self):
        provider = im_gateway.WeComProvider(
            _ConfigStub(
                {
                    "providers": {
                        "wecom": {
                            "enabled": True,
                            "bot_id": "bot-1",
                            "secret": "secret-1",
                        }
                    }
                }
            )
        )

        class _FailingClient:
            async def reply_stream(self, frame, stream_id, content, finish):
                raise RuntimeError("authentication expired")

        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever)
        loop_thread.start()
        provider.attach_client(_FailingClient(), loop, lambda prefix: f"{prefix}-1")
        frame = {"headers": {"req_id": "req-1"}, "body": {"text": {"content": "hello"}}}
        try:
            with patch.object(im_gateway_runtime, "write_im_gateway_status") as write_status:
                with self.assertRaisesRegex(RuntimeError, "企业微信回复失败"):
                    provider.send_card_reply({"sdk_frame": frame}, card_content="处理中")
            write_status.assert_called_once()
            self.assertEqual(write_status.call_args.args[:2], ("wecom", "error"))
        finally:
            loop.call_soon_threadsafe(loop.stop)
            loop_thread.join(timeout=2)
            loop.close()


if __name__ == "__main__":
    unittest.main()
