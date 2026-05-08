import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import im_gateway


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
    def load_config(self):
        return None

    def get(self, key, default=None):
        if key == "default_workspace":
            return "D:\\code\\cowork"
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


if __name__ == "__main__":
    unittest.main()
