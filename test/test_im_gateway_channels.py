import asyncio
import io
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.im_gateway import runtime as im_gateway
from core.im_gateway.wechat_ilink import (
    WeChatIlinkClient,
    WeChatIlinkError,
    WeChatQrExpired,
    WeChatTokenExpired,
    WeChatVerifyCodeBlocked,
    parse_wechat_updates,
    validate_ilink_base_url,
)
from core.im_gateway_config import (
    IM_PROVIDER_ORDER,
    normalize_im_gateway_config,
    update_selected_provider,
)
from core.im_gateway_registry import (
    ARTIFACT_DELIVERY_LINK,
    ARTIFACT_DELIVERY_NATIVE,
    ARTIFACT_DELIVERY_NONE,
    IM_PROVIDER_SPECS,
    artifact_capable_provider_ids,
    get_provider_spec,
)
from core.im_gateway_status import read_im_gateway_status, write_im_gateway_status


class _ConfigStub:
    def __init__(self, config=None):
        self.config = config or {}

    def get(self, key, default=None):
        if key == "im_gateway":
            return self.config
        if key == "default_workspace":
            return "D:\\code\\cowork"
        return default

    def get_god_mode(self):
        return False

    def load_config(self):
        return None


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _AsyncHttpClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class IMGatewayRegistryTests(unittest.TestCase):
    def test_registry_drives_five_single_provider_configs(self):
        self.assertEqual(
            IM_PROVIDER_ORDER,
            ("feishu", "dingtalk", "wecom", "qq", "wechat"),
        )
        self.assertEqual(
            [spec.provider_id for spec in IM_PROVIDER_SPECS],
            list(IM_PROVIDER_ORDER),
        )
        normalized = normalize_im_gateway_config(
            {
                "enabled_providers": ["qq", "wechat"],
                "providers": {
                    "qq": {
                        "enabled": True,
                        "app_id": "qq-app",
                        "client_secret": "qq-secret",
                    },
                    "wechat": {
                        "enabled": True,
                        "bot_token": "wx-token",
                        "ilink_bot_id": "wx-bot",
                    },
                },
            }
        )
        self.assertEqual(normalized["enabled_providers"], ["qq"])
        self.assertFalse(normalized["providers"]["wechat"]["enabled"])
        switched = update_selected_provider(
            normalized,
            "wechat",
            {"bot_token": "wx-token", "ilink_bot_id": "wx-bot"},
        )
        self.assertEqual(switched["enabled_providers"], ["wechat"])
        self.assertEqual(switched["providers"]["qq"]["app_id"], "qq-app")
        self.assertTrue(get_provider_spec("wechat").is_configured(
            switched["providers"]["wechat"]
        ))
        self.assertTrue(
            all(spec.runtime_adapter and spec.runtime_entry and spec.event_types
                for spec in IM_PROVIDER_SPECS)
        )
        self.assertEqual(get_provider_spec("feishu").artifact_delivery_mode, ARTIFACT_DELIVERY_NATIVE)
        self.assertEqual(get_provider_spec("dingtalk").artifact_delivery_mode, ARTIFACT_DELIVERY_LINK)
        self.assertEqual(get_provider_spec("wecom").artifact_delivery_mode, ARTIFACT_DELIVERY_LINK)
        self.assertEqual(get_provider_spec("qq").artifact_delivery_mode, ARTIFACT_DELIVERY_NONE)
        self.assertEqual(get_provider_spec("wechat").artifact_delivery_mode, ARTIFACT_DELIVERY_NONE)
        self.assertEqual(artifact_capable_provider_ids(), ("feishu", "dingtalk", "wecom"))

    def test_channel_model_input_describes_actual_delivery_capability(self):
        feishu_prompt = im_gateway._build_model_input({"text": "hello"}, "feishu")
        dingtalk_prompt = im_gateway._build_model_input({"text": "hello"}, "dingtalk")
        qq_prompt = im_gateway._build_model_input({"text": "hello"}, "qq")

        self.assertIn("本地文件、图片或链接", feishu_prompt)
        self.assertIn("仅能交付可访问的 URL", dingtalk_prompt)
        self.assertIn("不提供 publish_artifacts", qq_prompt)
        self.assertNotIn("若需要交付本地文件", qq_prompt)

    def test_sensitive_values_are_redacted(self):
        sanitized = im_gateway._sanitize(
            {
                "bot_token": "wx-token",
                "client_secret": "qq-secret",
                "user_id": "openid",
                "nested": {"context_token": "context"},
            }
        )
        self.assertEqual(sanitized["bot_token"], "<redacted>")
        self.assertEqual(sanitized["client_secret"], "<redacted>")
        self.assertEqual(sanitized["user_id"], "<redacted>")
        self.assertEqual(sanitized["nested"]["context_token"], "<redacted>")

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            im_gateway,
            "get_app_data_dir",
            return_value=temp_dir,
        ):
            im_gateway._log_gateway(
                'Authorization: Bearer access-secret "bot_token": "wx-secret"'
            )
            with open(
                os.path.join(temp_dir, "im_gateway.log"),
                "r",
                encoding="utf-8",
            ) as handle:
                log_text = handle.read()
        self.assertNotIn("access-secret", log_text)
        self.assertNotIn("wx-secret", log_text)

        stderr = io.StringIO()
        with patch.object(
            im_gateway,
            "get_app_data_dir",
            side_effect=OSError("readonly"),
        ), redirect_stderr(stderr):
            im_gateway._log_gateway(
                'Authorization: Bearer fallback-secret "bot_token": "fallback-token"'
            )
        fallback_text = stderr.getvalue()
        self.assertNotIn("fallback-secret", fallback_text)
        self.assertNotIn("fallback-token", fallback_text)
        self.assertIn("<redacted>", fallback_text)

        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "core.im_gateway_status.get_app_data_dir",
            return_value=temp_dir,
        ):
            write_im_gateway_status(
                "wechat",
                "error",
                "app_id=qq-app ilink_bot_id=wx-bot response_code=qr-token",
            )
            status = read_im_gateway_status()
        self.assertNotIn("qq-app", status["error"])
        self.assertNotIn("wx-bot", status["error"])
        self.assertNotIn("qr-token", status["error"])

    def test_unsupported_qq_and_wechat_messages_receive_a_clear_notice(self):
        class Provider:
            name = "wechat"

            def __init__(self):
                self.replies = []

            def parse_event(self, payload):
                return dict(payload)

            def send_card_reply(self, event, card_content="", title="", **kwargs):
                self.replies.append(card_content)

        provider = Provider()
        result = im_gateway._handle_im_event(
            {
                "event_type": "message",
                "sender_type": "user",
                "message_type": "unsupported",
                "unsupported_type": 2,
                "message_id": "wx-1",
                "user_id": "user-1",
                "chat_id": "user-1",
                "text": "",
            },
            provider,
            object(),
            _ConfigStub(),
            object(),
        )
        self.assertIsNone(result)
        self.assertEqual(
            provider.replies,
            ["当前版本仅支持文字和链接，请换成文字消息再试。"],
        )


class WeChatIlinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_qr_updates_and_send_use_official_ilink_shapes(self):
        http = _AsyncHttpClient(
            [
                _Response(
                    {
                        "qrcode": "opaque-qr",
                        "qrcode_img_content": "https://weixin.qq.com/q/test",
                    }
                ),
                _Response(
                    {
                        "status": "confirmed",
                        "bot_token": "wx-token",
                        "ilink_bot_id": "wx-bot",
                        "ilink_user_id": "wx-user",
                        "baseurl": "https://ilinkai.weixin.qq.com",
                    }
                ),
                _Response(
                    {
                        "ret": 0,
                        "errcode": 0,
                        "get_updates_buf": "cursor-2",
                        "msgs": [
                            {
                                "message_type": 1,
                                "message_id": 1001,
                                "from_user_id": "user@im.wechat",
                                "create_time_ms": 123,
                                "context_token": "ctx",
                                "item_list": [
                                    {
                                        "type": 1,
                                        "text_item": {
                                            "text": "查看 https://example.com"
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ),
                _Response({"ret": 0, "errcode": 0, "message_id": "reply-1"}),
            ]
        )
        client = WeChatIlinkClient(http_client=http)
        qr = await client.create_qr_code()
        status = await client.poll_qr_status(qr.qrcode)
        credentials = client.credentials_from_status(status)
        updates = await client.get_updates(credentials.bot_token, "cursor-1")
        events = parse_wechat_updates(updates)
        message_id = await client.send_text(
            credentials.bot_token,
            events[0]["user_id"],
            events[0]["context_token"],
            "已收到",
        )

        self.assertEqual(qr.qrcode, "opaque-qr")
        self.assertEqual(credentials.ilink_bot_id, "wx-bot")
        self.assertEqual(events[0]["message_type"], "text")
        self.assertIn("https://example.com", events[0]["text"])
        self.assertEqual(message_id, "reply-1")
        self.assertEqual(
            [call[0] for call in http.calls],
            ["POST", "GET", "POST", "POST"],
        )
        self.assertEqual(
            http.calls[2][2]["headers"]["Authorization"],
            "Bearer wx-token",
        )
        self.assertEqual(
            http.calls[3][2]["json"]["msg"]["context_token"],
            "ctx",
        )

    async def test_token_expiry_is_explicit(self):
        client = WeChatIlinkClient(
            http_client=_AsyncHttpClient(
                [_Response({"ret": -14, "errcode": -14, "errmsg": "expired"})]
            )
        )
        with self.assertRaisesRegex(WeChatTokenExpired, "重新扫码"):
            await client.get_updates("expired-token")

    async def test_qr_expiry_and_verify_block_are_explicit(self):
        expired = WeChatIlinkClient(
            http_client=_AsyncHttpClient([_Response({"status": "expired"})])
        )
        with self.assertRaisesRegex(WeChatQrExpired, "重新生成"):
            await expired.poll_qr_status("opaque")

        blocked = WeChatIlinkClient(
            http_client=_AsyncHttpClient(
                [_Response({"status": "verify_code_blocked"})]
            )
        )
        with self.assertRaisesRegex(WeChatVerifyCodeBlocked, "稍后重新扫码"):
            await blocked.poll_qr_status("opaque", verify_code="123456")

    def test_non_text_update_is_preserved_for_user_notice(self):
        events = parse_wechat_updates(
            {
                "msgs": [
                    {
                        "message_type": 1,
                        "message_id": 2,
                        "from_user_id": "user",
                        "item_list": [{"type": 2, "image_item": {}}],
                    },
                    {
                        "message_type": 2,
                        "message_id": 3,
                        "from_user_id": "bot",
                        "item_list": [{"type": 1, "text_item": {"text": "bot"}}],
                    },
                ]
            }
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["message_type"], "unsupported")
        self.assertEqual(events[0]["unsupported_type"], 2)

    def test_ilink_base_url_rejects_untrusted_hosts(self):
        self.assertEqual(
            validate_ilink_base_url("https://ilinkai.weixin.qq.com/path"),
            "https://ilinkai.weixin.qq.com",
        )
        with self.assertRaises(WeChatIlinkError):
            validate_ilink_base_url("https://example.com")
        with self.assertRaises(WeChatIlinkError):
            validate_ilink_base_url("https://ilinkai.weixin.qq.com:bad")
        with self.assertRaises(WeChatIlinkError):
            validate_ilink_base_url("https://ilinkai.weixin.qq.com:8443")


class ProviderBridgeTests(unittest.TestCase):
    def _run_loop(self):
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever)
        thread.start()
        self.addCleanup(loop.close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(loop.call_soon_threadsafe, loop.stop)
        return loop

    def test_qq_provider_replies_through_official_sdk_client(self):
        class Api:
            def __init__(self):
                self.calls = []

            async def send_text(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return {"id": "qq-reply"}

        provider = im_gateway.QQProvider(
            _ConfigStub(
                {
                    "providers": {
                        "qq": {
                            "app_id": "qq-app",
                            "client_secret": "qq-secret",
                        }
                    }
                }
            )
        )
        api = Api()
        loop = self._run_loop()
        provider.attach_client(api, loop)
        result = provider.send_message(
            "回复",
            {
                "chat_scope": "group",
                "chat_id": "group-1",
                "message_id": "message-1",
            },
        )
        self.assertEqual(result, "qq-reply")
        self.assertEqual(api.calls[0][0][:3], ("group", "group-1", "回复"))
        self.assertEqual(api.calls[0][1]["reply_to"], "message-1")

    def test_wechat_provider_replies_with_context_token(self):
        class Client:
            def __init__(self):
                self.calls = []

            async def send_text(self, *args):
                self.calls.append(args)
                return "wx-reply"

        provider = im_gateway.WeChatProvider(
            _ConfigStub(
                {
                    "providers": {
                        "wechat": {
                            "bot_token": "wx-token",
                            "ilink_bot_id": "wx-bot",
                        }
                    }
                }
            )
        )
        client = Client()
        loop = self._run_loop()
        provider.attach_client(client, loop)
        result = provider.send_message(
            "回复",
            {"user_id": "wx-user", "context_token": "ctx"},
        )
        self.assertEqual(result, "wx-reply")
        self.assertEqual(
            client.calls[0],
            ("wx-token", "wx-user", "ctx", "回复"),
        )

    def test_wechat_send_token_expiry_signals_runtime_stop(self):
        class Client:
            async def send_text(self, *_args):
                raise WeChatTokenExpired("expired")

        provider = im_gateway.WeChatProvider(
            _ConfigStub(
                {
                    "providers": {
                        "wechat": {
                            "bot_token": "wx-token",
                            "ilink_bot_id": "wx-bot",
                        }
                    }
                }
            )
        )
        loop = self._run_loop()
        expired_event = threading.Event()
        provider.attach_client(Client(), loop, expired_event)
        with self.assertRaisesRegex(RuntimeError, "重新扫码"):
            provider.send_message(
                "回复",
                {"user_id": "wx-user", "context_token": "ctx"},
            )
        self.assertTrue(expired_event.wait(1))


if __name__ == "__main__":
    unittest.main()
