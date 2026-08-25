import unittest

from core.conversation_integrity import (
    LedgerMessageConflictError,
    ToolSequenceValidationError,
    canonical_ledger_messages_hash,
    ensure_tool_call_sequence,
    merge_messages_by_id,
    normalize_message_ids,
    validate_tool_call_sequence,
)
from core.agent import sanitize_llm_messages
from core.llm.responses_replay import (
    PROVIDER_REPLAY_NAMESPACE_META_KEY,
    RESPONSES_REPLAY_META_KEY,
    build_provider_replay_namespace,
)


def _assistant_call(call_id, name="lookup"):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
        ],
    }


class TestConversationIntegrity(unittest.TestCase):
    def test_missing_tool_result_is_rejected_with_call_id(self):
        validation = validate_tool_call_sequence([
            _assistant_call("call-1"),
            {"role": "user", "content": "continue"},
        ])

        self.assertFalse(validation.valid)
        self.assertEqual(validation.missing_tool_call_ids, ("call-1",))
        with self.assertRaisesRegex(ToolSequenceValidationError, "call-1"):
            ensure_tool_call_sequence(
                [_assistant_call("call-1")],
                context="Chat Completions",
            )

    def test_multiple_tool_calls_require_all_matching_results(self):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "one", "arguments": "{}"},
                    },
                    {
                        "id": "call-2",
                        "type": "function",
                        "function": {"name": "two", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "one"},
            {"role": "tool", "tool_call_id": "call-2", "content": "two"},
        ]

        self.assertTrue(validate_tool_call_sequence(messages).valid)

    def test_multiple_tool_results_must_follow_assistant_call_order(self):
        validation = validate_tool_call_sequence([
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "one", "arguments": "{}"},
                    },
                    {
                        "id": "call-2",
                        "type": "function",
                        "function": {"name": "two", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call-2", "content": "two"},
            {"role": "tool", "tool_call_id": "call-1", "content": "one"},
        ])

        self.assertFalse(validation.valid)
        self.assertEqual(validation.out_of_order_tool_call_ids, ("call-2",))

    def test_responses_replay_function_calls_are_paired_with_tool_results(self):
        messages = [
            {
                "role": "assistant",
                "_responses_replay_items": [
                    {
                        "type": "reasoning",
                        "id": "rs-1",
                        "summary": [],
                        "content": [],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "lookup",
                        "arguments": "{}",
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ]

        self.assertTrue(validate_tool_call_sequence(messages).valid)

    def test_responses_replay_projects_to_chat_completions_without_mutating_ledger(self):
        messages = [
            {
                "id": "assistant-1",
                "role": "assistant",
                "content": None,
                "meta": {
                    RESPONSES_REPLAY_META_KEY: [
                        {
                            "type": "reasoning",
                            "id": "rs-1",
                            "summary": [],
                            "content": [],
                        },
                        {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "lookup",
                            "arguments": "{}",
                        },
                    ],
                },
            },
            {"id": "tool-1", "role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ]

        projected = sanitize_llm_messages(
            messages,
            project_responses_replay_to_chat=True,
        )

        self.assertEqual(
            projected[0]["tool_calls"][0]["id"],
            "call-1",
        )
        self.assertNotIn(RESPONSES_REPLAY_META_KEY, projected[0])
        self.assertNotIn("tool_calls", messages[0])
        self.assertIn(RESPONSES_REPLAY_META_KEY, messages[0]["meta"])

    def test_model_switch_projects_completed_tool_facts_without_rewriting_ledger(self):
        source_namespace = build_provider_replay_namespace(
            provider_family="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-old",
            protocol="responses",
        )
        target_namespace = build_provider_replay_namespace(
            provider_family="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-new",
            protocol="responses",
        )
        messages = [
            {"id": "user-1", "role": "user", "content": "lookup"},
            {
                "id": "assistant-1",
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }],
                "meta": {
                    RESPONSES_REPLAY_META_KEY: [{
                        "id": "fc-1",
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "lookup",
                        "arguments": "{}",
                    }],
                    PROVIDER_REPLAY_NAMESPACE_META_KEY: source_namespace,
                },
            },
            {
                "id": "tool-1",
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "result",
            },
        ]
        original_hash = canonical_ledger_messages_hash(messages)

        projected, metadata = sanitize_llm_messages(
            messages,
            require_reasoning_replay=True,
            return_metadata=True,
            target_replay_namespace=target_namespace,
        )

        self.assertEqual(canonical_ledger_messages_hash(messages), original_hash)
        self.assertEqual(len(projected), 2)
        self.assertNotIn("tool_calls", projected[1])
        self.assertIn("已完成的历史工具记录", projected[1]["content"])
        self.assertEqual(
            metadata["protocol_tool_round_projections"][0]["tool_call_ids"],
            ["call-1"],
        )

    def test_incomplete_tool_round_blocks_projection_without_rewriting_ledger(self):
        messages = [
            {"id": "user-1", "role": "user", "content": "lookup"},
            {
                "id": "assistant-1",
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }],
            },
        ]
        original_hash = canonical_ledger_messages_hash(messages)
        target_namespace = build_provider_replay_namespace(
            provider_family="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-new",
            protocol="chat_completions",
        )

        with self.assertRaisesRegex(ToolSequenceValidationError, "原历史不会被静默裁剪"):
            sanitize_llm_messages(
                messages,
                require_reasoning_replay=True,
                target_replay_namespace=target_namespace,
            )

        self.assertEqual(canonical_ledger_messages_hash(messages), original_hash)

    def test_chat_to_responses_projects_tool_facts_without_fabricating_reasoning(self):
        messages = [
            {"id": "user-1", "role": "user", "content": "lookup"},
            {
                "id": "assistant-1",
                "role": "assistant",
                "content": "",
                "reasoning_content": "chat-only reasoning",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": "{}"},
                }],
            },
            {
                "id": "tool-1",
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "result",
            },
        ]
        original_hash = canonical_ledger_messages_hash(messages)
        target_namespace = build_provider_replay_namespace(
            provider_family="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-new",
            protocol="responses",
        )

        projected = sanitize_llm_messages(
            messages,
            require_reasoning_replay=True,
            preserve_all_reasoning=True,
            strict_reasoning_replay=True,
            target_replay_namespace=target_namespace,
        )

        self.assertEqual(canonical_ledger_messages_hash(messages), original_hash)
        self.assertEqual(len(projected), 2)
        self.assertNotIn("reasoning_content", projected[1])
        self.assertNotIn("tool_calls", projected[1])
        self.assertIn("result", projected[1]["content"])

    def test_duplicate_ids_are_deterministically_remapped_without_content_deduplication(self):
        source = [
            {"id": "same", "role": "user", "content": "same text"},
            {"id": "same", "role": "user", "content": "same text"},
        ]

        first, first_repairs = normalize_message_ids(source, conversation_id="conv-1")
        second, second_repairs = normalize_message_ids(source, conversation_id="conv-1")

        self.assertEqual([item["id"] for item in first], [item["id"] for item in second])
        self.assertEqual([item["content"] for item in first], ["same text", "same text"])
        self.assertEqual(len(first_repairs), 1)
        self.assertEqual(first_repairs, second_repairs)

    def test_same_message_id_different_payload_is_a_ledger_conflict(self):
        with self.assertRaises(LedgerMessageConflictError):
            merge_messages_by_id(
                [{"id": "m1", "role": "user", "content": "原始内容"}],
                [{"id": "m1", "role": "user", "content": "被改写内容"}],
            )

    def test_same_message_id_same_payload_is_idempotent(self):
        merged = merge_messages_by_id(
            [{"id": "m1", "role": "user", "content": "同一事件"}],
            [{"id": "m1", "role": "user", "content": "同一事件"}],
        )
        self.assertEqual(len(merged), 1)


if __name__ == "__main__":
    unittest.main()
