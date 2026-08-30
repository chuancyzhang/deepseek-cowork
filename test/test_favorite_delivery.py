import asyncio
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.chat_storage import ChatStorage
from core.favorite_delivery import (
    FAVORITE_DELIVERY_STATUS_COMPLETED,
    FAVORITE_DELIVERY_STATUS_UNKNOWN,
    FavoriteDeliveryService,
    collect_feishu_artifacts,
    normalize_favorite_delivery,
    parse_favorite_binding_command,
    prepare_binding_target,
    split_delivery_text,
)
from core.im_gateway_registry import scheduled_delivery_provider_ids
from core.im_gateway import runtime as im_gateway


class FavoriteDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "chat.sqlite")
        self.storage = ChatStorage(self.db_path)
        self.service = FavoriteDeliveryService(self.storage)

    def tearDown(self):
        self.temp.cleanup()

    def _bind(self, provider="feishu", target_type="chat_id", target_value="chat-1"):
        request = self.service.create_binding_request("fav-1", now_ts=1000)
        pending = self.service.find_pending_request(request["code"], now_ts=1001)
        return self.service.claim_binding_request(
            pending["request_id"],
            provider,
            {
                "target_type": target_type,
                "target_value": target_value,
                "display_name": "测试会话",
            },
            now_ts=1002,
        )

    def test_delivery_config_requires_binding_only_when_enabled(self):
        self.assertIsNone(normalize_favorite_delivery(None))
        self.assertEqual(
            normalize_favorite_delivery({"enabled": False}),
            {"enabled": False, "binding_id": ""},
        )
        with self.assertRaisesRegex(ValueError, "完成目标绑定"):
            normalize_favorite_delivery({"enabled": True})

    def test_binding_code_is_hashed_one_time_and_expires(self):
        request = self.service.create_binding_request("fav-1", now_ts=1000)
        self.assertEqual(parse_favorite_binding_command(request["command"]), request["code"])
        with self.storage._connect() as conn:
            row = conn.execute(
                "SELECT code_hash, code_salt FROM favorite_delivery_binding_requests WHERE id = ?",
                (request["request_id"],),
            ).fetchone()
        self.assertNotIn(request["code"], (row["code_hash"], row["code_salt"]))
        pending = self.service.find_pending_request(request["code"], now_ts=1001)
        binding = self.service.claim_binding_request(
            pending["request_id"],
            "feishu",
            {"target_type": "chat_id", "target_value": "chat-1", "display_name": "群聊"},
            now_ts=1002,
        )
        self.assertEqual(binding["provider"], "feishu")
        self.assertNotIn("target_value", binding)
        self.assertIsNone(self.service.find_pending_request(request["code"], now_ts=1003))
        with self.assertRaisesRegex(ValueError, "已使用"):
            self.service.claim_binding_request(
                pending["request_id"],
                "feishu",
                {"target_type": "chat_id", "target_value": "chat-1"},
                now_ts=1003,
            )

        expired = self.service.create_binding_request("fav-2", now_ts=2000)
        self.assertIsNone(self.service.find_pending_request(expired["code"], now_ts=2600))

    def test_pending_binding_codes_are_unique(self):
        with patch(
            "core.favorite_delivery.secrets.randbelow",
            side_effect=[123456, 123456, 654321],
        ):
            first = self.service.create_binding_request("fav-code-1", now_ts=1000)
            second = self.service.create_binding_request("fav-code-2", now_ts=1001)
        self.assertEqual(first["code"], "123456")
        self.assertEqual(second["code"], "654321")

    def test_supported_targets_are_strict(self):
        self.assertEqual(scheduled_delivery_provider_ids(), ("feishu", "dingtalk", "wecom"))
        self.assertEqual(
            prepare_binding_target("feishu", {"chat_id": "chat-a"})["target_value"],
            "chat-a",
        )
        self.assertEqual(
            prepare_binding_target("wecom", {"user_id": "user-a"})["target_type"],
            "user_id",
        )
        with self.assertRaisesRegex(ValueError, "固定 Webhook"):
            prepare_binding_target("dingtalk", {"chat_id": "chat-a"}, {})
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            prepare_binding_target(
                "dingtalk",
                {"chat_id": "chat-a"},
                {"webhook_url": "http://example.test/hook"},
            )
        with self.assertRaisesRegex(ValueError, "暂不支持"):
            prepare_binding_target("wechat", {"user_id": "user-a"})

    def test_enqueue_is_idempotent_by_run_and_feishu_gets_artifacts(self):
        binding = self._bind()
        artifact = os.path.join(self.temp.name, "report.txt")
        with open(artifact, "w", encoding="utf-8") as handle:
            handle.write("ok")
        job = self.service.enqueue_delivery(
            run_history_id="run-1",
            favorite_id="fav-1",
            session_id="session-1",
            binding_id=binding["id"],
            favorite_name="日报",
            terminal_status="completed",
            content="结果正文",
            artifacts=[artifact],
            now_ts=1100,
        )
        item_types = [item["type"] for item in job["payload"]["items"]]
        self.assertEqual(item_types, ["text", "artifact"])
        with self.assertRaises(Exception):
            self.service.enqueue_delivery(
                run_history_id="run-1",
                favorite_id="fav-1",
                session_id="session-2",
                binding_id=binding["id"],
                favorite_name="日报",
                terminal_status="completed",
                content="重复",
            )

    def test_dingtalk_and_wecom_do_not_enqueue_local_artifacts(self):
        for provider, target_type in (("dingtalk", "webhook"), ("wecom", "chat_id")):
            with self.subTest(provider=provider):
                favorite_id = f"fav-{provider}"
                request = self.service.create_binding_request(favorite_id, now_ts=1000)
                pending = self.service.find_pending_request(request["code"], now_ts=1001)
                binding = self.service.claim_binding_request(
                    pending["request_id"],
                    provider,
                    {"target_type": target_type, "target_value": f"target-{provider}"},
                    now_ts=1002,
                )
                job = self.service.enqueue_delivery(
                    run_history_id=f"run-{provider}",
                    favorite_id=favorite_id,
                    session_id="session-1",
                    binding_id=binding["id"],
                    favorite_name="日报",
                    terminal_status="completed",
                    content="结果",
                    artifacts=[__file__],
                    now_ts=1100,
                )
                self.assertEqual([item["type"] for item in job["payload"]["items"]], ["text"])

    def test_claim_complete_retry_and_crash_recovery(self):
        binding = self._bind()
        job = self.service.enqueue_delivery(
            run_history_id="run-state",
            favorite_id="fav-1",
            session_id="session-1",
            binding_id=binding["id"],
            favorite_name="日报",
            terminal_status="completed",
            content="结果",
            now_ts=1100,
        )
        claimed = self.service.claim_next_job("feishu", now_ts=1101)
        self.assertEqual(claimed["attempt_count"], 1)
        payload = claimed["payload"]
        payload["items"][0]["status"] = "sent"
        completed = self.service.save_job_state(
            job["id"], payload, FAVORITE_DELIVERY_STATUS_COMPLETED, now_ts=1102
        )
        self.assertEqual(completed["status"], FAVORITE_DELIVERY_STATUS_COMPLETED)
        self.assertIsNone(self.service.retry_job(job["id"], now_ts=1103))

        interrupted = self.service.enqueue_delivery(
            run_history_id="run-interrupted",
            favorite_id="fav-1",
            session_id="session-2",
            binding_id=binding["id"],
            favorite_name="日报",
            terminal_status="completed",
            content="未确认结果",
            now_ts=1104,
        )
        self.service.claim_next_job("feishu", now_ts=1105)
        recovered_storage = ChatStorage(self.db_path)
        recovered_service = FavoriteDeliveryService(recovered_storage)
        self.assertEqual(recovered_service.get_job(interrupted["id"])["status"], "sending")
        self.assertEqual(recovered_service.recover_interrupted_jobs(now_ts=1106), 1)
        recovered = recovered_service.get_job(interrupted["id"])
        self.assertEqual(recovered["status"], FAVORITE_DELIVERY_STATUS_UNKNOWN)

    def test_text_chunks_and_artifact_workspace_boundary(self):
        chunks = split_delivery_text("A" * 8000, limit=1000)
        self.assertEqual("".join(chunks), "A" * 8000)
        inside = os.path.join(self.temp.name, "inside.txt")
        with open(inside, "w", encoding="utf-8") as handle:
            handle.write("inside")
        outside_temp = tempfile.NamedTemporaryFile(delete=False)
        outside_temp.close()
        try:
            self.assertEqual(
                collect_feishu_artifacts([inside, inside, outside_temp.name], self.temp.name),
                [inside],
            )
            self.assertEqual(collect_feishu_artifacts([inside], ""), [])
        finally:
            os.unlink(outside_temp.name)

    def test_gateway_worker_retries_definite_failure_and_stops_on_unknown(self):
        binding = self._bind()
        job = self.service.enqueue_delivery(
            run_history_id="run-retry",
            favorite_id="fav-1",
            session_id="session-1",
            binding_id=binding["id"],
            favorite_name="日报",
            terminal_status="completed",
            content="结果",
            now_ts=1100,
        )
        claimed = self.service.claim_next_job("feishu", now_ts=1101)

        class RetryProvider:
            def send_favorite_delivery_item(self, item, target):
                return {"ok": False, "retryable": True, "error": "temporary"}

        with patch("core.im_gateway.runtime.time.time", return_value=1101):
            im_gateway._process_favorite_delivery_job(self.service, RetryProvider(), claimed)
        waiting = self.service.get_job(job["id"])
        self.assertEqual(waiting["status"], "retry_wait")
        self.assertGreater(waiting["next_attempt_at"], 1101)
        self.service.save_job_state(
            waiting["id"], waiting["payload"], "failed", error="test complete", now_ts=1102
        )

        unknown_binding = self._bind(target_value="chat-2")
        unknown_job = self.service.enqueue_delivery(
            run_history_id="run-unknown",
            favorite_id="fav-1",
            session_id="session-2",
            binding_id=unknown_binding["id"],
            favorite_name="日报",
            terminal_status="completed",
            content="结果",
            now_ts=1200,
        )
        unknown_claimed = self.service.claim_next_job("feishu", now_ts=1201)

        class UnknownProvider:
            def send_favorite_delivery_item(self, item, target):
                return {"ok": False, "ambiguous": True, "error": "timeout"}

        im_gateway._process_favorite_delivery_job(self.service, UnknownProvider(), unknown_claimed)
        self.assertEqual(self.service.get_job(unknown_job["id"])["status"], "unknown")

    def test_gateway_binding_confirmation_precedes_claim(self):
        request = self.service.create_binding_request("fav-gateway", now_ts=int(__import__("time").time()))

        class Config:
            def get(self, key, default=None):
                if key == "im_gateway":
                    return {
                        "providers": {"dingtalk": {"webhook_url": "https://example.com/hook"}}
                    }
                return default

        class Provider:
            name = "dingtalk"

            def __init__(self):
                self.confirmed = False

            def send_favorite_delivery_item(self, item, target):
                self.confirmed = True
                self.assert_target = target
                return {"ok": True}

            def send_card_reply(self, *args, **kwargs):
                raise AssertionError("successful binding must use proactive confirmation")

        provider = Provider()
        consumed = im_gateway._handle_favorite_binding_command(
            {"text": request["command"], "chat_id": "chat-1"},
            provider,
            Config(),
            self.service,
        )
        self.assertTrue(consumed)
        self.assertTrue(provider.confirmed)
        binding = self.service.get_binding(request["binding_id"])
        self.assertEqual(binding["provider"], "dingtalk")
        self.assertEqual(binding["target_type"], "webhook")

    def test_feishu_proactive_text_uses_stable_idempotency_key(self):
        class Config:
            def get(self, key, default=None):
                if key == "im_gateway":
                    return {"providers": {"feishu": {"app_id": "a", "app_secret": "s"}}}
                return default

        provider = im_gateway.FeishuProvider(Config())
        response = SimpleNamespace(
            ok=True,
            status_code=200,
            json=lambda: {"code": 0, "data": {"message_id": "m-1"}},
            text="",
        )
        with patch.object(provider, "_get_tenant_token", return_value="token"):
            with patch.object(im_gateway.requests, "post", return_value=response) as request:
                result = provider.send_favorite_delivery_item(
                    {"type": "text", "text": "结果", "idempotency_key": "stable-key"},
                    {"target_type": "chat_id", "target_value": "chat-1"},
                )
        self.assertTrue(result["ok"])
        self.assertEqual(request.call_args.kwargs["json"]["uuid"], "stable-key")

    def test_dingtalk_timeout_is_unknown_and_redacts_webhook(self):
        class Config:
            def get(self, key, default=None):
                if key == "im_gateway":
                    return {"providers": {"dingtalk": {"webhook_url": "https://example.test/token"}}}
                return default

        provider = im_gateway.DingTalkProvider(Config())
        with patch.object(
            im_gateway.requests,
            "post",
            side_effect=im_gateway.requests.Timeout("https://example.test/token timed out"),
        ):
            result = provider.send_favorite_delivery_item(
                {"type": "text", "text": "结果"},
                {"target_type": "webhook", "target_value": "https://example.test/token"},
            )
        self.assertTrue(result["ambiguous"])
        self.assertNotIn("example.test", result["error"])

    def test_wecom_proactive_text_uses_connected_sdk(self):
        class Config:
            def get(self, key, default=None):
                if key == "im_gateway":
                    return {"providers": {"wecom": {"bot_id": "b", "secret": "s"}}}
                return default

        class Client:
            def __init__(self):
                self.calls = []

            async def send_message(self, target, payload):
                self.calls.append((target, payload))
                return {"ok": True}

        provider = im_gateway.WeComProvider(Config())
        client = Client()
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever)
        thread.start()
        provider.attach_client(client, loop, lambda prefix: f"{prefix}-1")
        try:
            result = provider.send_favorite_delivery_item(
                {"type": "text", "text": "结果"},
                {"target_type": "chat_id", "target_value": "chat-1"},
            )
            self.assertTrue(result["ok"])
            self.assertEqual(client.calls[0][0], "chat-1")
            self.assertEqual(client.calls[0][1]["markdown"]["content"], "结果")
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=2)
            loop.close()


if __name__ == "__main__":
    unittest.main()
