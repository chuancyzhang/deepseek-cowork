import unittest

from core.conversation_render import (
    build_conversation_render_items,
    build_conversation_render_spans,
    is_legacy_skill_change_notice_message,
)


class TestConversationRender(unittest.TestCase):
    def test_builds_basic_user_and_assistant_items(self):
        items = build_conversation_render_items(
            [
                {"id": "u1", "role": "user", "content": "hello"},
                {"id": "a1", "role": "assistant", "content": "world", "reasoning": "thinking"},
            ]
        )
        self.assertEqual([item["type"] for item in items], ["user", "assistant"])
        self.assertEqual(items[1]["content"], "world")
        self.assertEqual(items[1]["reasoning"], "thinking")

    def test_legacy_skill_change_notices_do_not_enter_items_or_spans(self):
        legacy_created = {
            "id": "legacy-created",
            "role": "assistant",
            "content": "◈ AI 已创建能力：demo，现已可用",
            "meta": {
                "ui_only": True,
                "skill_change_event_id": "event-created",
                "skill_change": {
                    "source": "ai",
                    "action": "created",
                    "skill_names": ["demo"],
                },
            },
        }
        legacy_enabled = {
            "id": "legacy-enabled",
            "role": "assistant",
            "content": "◈ 已启用能力：demo，可被 AI 发现",
            "meta": {
                "ui_only": True,
                "skill_change": {
                    "source": "ui",
                    "action": "enabled",
                    "skill_names": ["demo"],
                },
            },
        }
        messages = [
            {"id": "u1", "role": "user", "content": "hello"},
            legacy_created,
            legacy_enabled,
            {"id": "a1", "role": "assistant", "content": "world"},
        ]

        self.assertTrue(is_legacy_skill_change_notice_message(legacy_created))
        self.assertTrue(is_legacy_skill_change_notice_message(legacy_enabled))
        self.assertEqual(
            [item["type"] for item in build_conversation_render_items(messages)],
            ["user", "assistant"],
        )
        self.assertEqual(
            build_conversation_render_spans(messages),
            [{"start": 0, "end": 1}, {"start": 3, "end": 4}],
        )
        self.assertEqual(len(messages), 4)

    def test_append_only_hidden_context_does_not_change_chat_projection(self):
        hidden_kinds = [
            "skill_context",
            "skill_state_update",
            "runtime_context",
            "runtime_context_update",
            "runtime_instruction",
        ]
        messages = [{"id": "u1", "role": "user", "content": "hello"}]
        for index, kind in enumerate(hidden_kinds):
            messages.append({
                "id": f"hidden-{index}",
                "role": "system",
                "content": f"hidden {kind}",
                "meta": {"hidden": True, "kind": kind},
            })
        messages.append({
            "id": "a1",
            "role": "assistant",
            "content": "world",
            "reasoning_content": "thinking",
            "meta": {"responses_replay_items": [{"type": "message", "id": "provider-a1"}]},
        })

        items = build_conversation_render_items(messages)

        self.assertEqual([item["type"] for item in items], ["user", "assistant"])
        self.assertEqual(items[0]["message"]["content"], "hello")
        self.assertEqual(items[1]["content"], "world")
        self.assertEqual(items[1]["reasoning"], "thinking")
        self.assertEqual(
            build_conversation_render_spans(messages),
            [{"start": 0, "end": 1}, {"start": 6, "end": 7}],
        )
        self.assertEqual(len(messages), 7)

    def test_embedded_agent_result_stays_out_of_chat_projection(self):
        messages = [
            {
                "id": "u1",
                "role": "user",
                "content": "@审查助手 检查方案",
                "meta": {
                    "agent_delegation": True,
                    "summoned_agents": [{"agent_id": "agent-1", "agent_profile_name": "审查助手"}],
                },
            },
            {
                "id": "a1",
                "role": "assistant",
                "content": "[审查助手] 已完成",
                "meta": {"agent_id": "agent-1", "embedded_agent_result": True},
            },
        ]

        self.assertEqual(
            [item["type"] for item in build_conversation_render_items(messages)],
            ["user"],
        )
        self.assertEqual(
            build_conversation_render_spans(messages),
            [{"start": 0, "end": 1}],
        )

    def test_merges_tool_calls_and_results_into_same_assistant_item(self):
        items = build_conversation_render_items(
            [
                {
                    "id": "a1",
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "step-by-step",
                    "tool_calls": [
                        {
                            "id": "tool-1",
                            "type": "function",
                            "function": {"name": "run_python_code", "arguments": {"code": "print(1)"}},
                        }
                    ],
                },
                {
                    "id": "t1",
                    "role": "tool",
                    "tool_call_id": "tool-1",
                    "content": "{\"ok\": true}",
                    "result_obj": {"ok": True},
                    "meta": {"duration": 0.4},
                },
                {"id": "a2", "role": "assistant", "content": "done"},
            ]
        )
        self.assertEqual(len(items), 1)
        assistant = items[0]
        self.assertEqual(assistant["type"], "assistant")
        self.assertEqual(assistant["content"], "done")
        self.assertEqual(assistant["reasoning"], "step-by-step")
        self.assertEqual(len(assistant["tool_calls"]), 1)
        self.assertEqual(assistant["tool_calls"][0]["name"], "run_python_code")
        self.assertEqual(assistant["tool_calls"][0]["result_obj"], {"ok": True})

    def test_keeps_multiple_tools_in_order(self):
        items = build_conversation_render_items(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "tool-1", "function": {"name": "a", "arguments": "{}"}},
                        {"id": "tool-2", "function": {"name": "b", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "tool-2", "content": "second"},
                {"role": "tool", "tool_call_id": "tool-1", "content": "first"},
            ]
        )
        tools = items[0]["tool_calls"]
        self.assertEqual([tool["id"] for tool in tools], ["tool-1", "tool-2"])
        self.assertEqual([tool["result"] for tool in tools], ["first", "second"])

    def test_handles_orphan_tool_result(self):
        items = build_conversation_render_items(
            [
                {"id": "t1", "role": "tool", "tool_call_id": "tool-1", "content": "result only"},
            ]
        )
        self.assertEqual(len(items), 1)
        assistant = items[0]
        self.assertEqual(assistant["type"], "assistant")
        self.assertEqual(assistant["tool_calls"][0]["id"], "tool-1")
        self.assertEqual(assistant["tool_calls"][0]["result"], "result only")

    def test_preserves_structured_content_parts(self):
        items = build_conversation_render_items(
            [
                {
                    "role": "assistant",
                    "content": "summary",
                    "content_parts": [
                        {"type": "text", "text": "summary"},
                        {"type": "text", "text": "details"},
                    ],
                }
            ]
        )
        self.assertEqual(len(items[0]["content_parts"]), 2)

    def test_build_conversation_render_spans_groups_non_user_clusters(self):
        spans = build_conversation_render_spans(
            [
                {"id": "u1", "role": "user", "content": "hello"},
                {"id": "a1", "role": "assistant", "content": "thinking"},
                {"id": "t1", "role": "tool", "tool_call_id": "tool-1", "content": "ok"},
                {"id": "a2", "role": "assistant", "content": "done"},
                {"id": "u2", "role": "user", "content": "next"},
            ]
        )
        self.assertEqual(spans, [{"start": 0, "end": 1}, {"start": 1, "end": 4}, {"start": 4, "end": 5}])

    def test_same_turn_guidance_stays_in_current_turn_span(self):
        spans = build_conversation_render_spans(
            [
                {"id": "u1", "role": "user", "content": "start"},
                {"id": "a1", "role": "assistant", "content": "working"},
                {"id": "g1", "role": "user", "content": "focus tests", "meta": {"same_turn_guidance": True}},
                {"id": "a2", "role": "assistant", "content": "done"},
                {"id": "u2", "role": "user", "content": "next"},
            ]
        )
        self.assertEqual(spans, [{"start": 0, "end": 1}, {"start": 1, "end": 4}, {"start": 4, "end": 5}])

    def test_same_turn_guidance_has_dedicated_render_item(self):
        items = build_conversation_render_items(
            [
                {"id": "a1", "role": "assistant", "content": "working"},
                {"id": "g1", "role": "user", "content": "focus tests", "meta": {"same_turn_guidance": True}},
                {"id": "a2", "role": "assistant", "content": "done"},
            ]
        )
        self.assertEqual([item["type"] for item in items], ["assistant", "guidance", "assistant"])

    def test_build_conversation_render_spans_keeps_office_draft_turn_collapsible(self):
        spans = build_conversation_render_spans(
            [
                {
                    "id": "u1",
                    "role": "user",
                    "content": "generate",
                    "meta": {"workflow_mode": "office_html_first", "office_output_profile": "ppt"},
                },
                {"id": "a1", "role": "assistant", "content": "working"},
                {"id": "t1", "role": "tool", "tool_call_id": "tool-1", "content": "ok"},
                {"id": "a2", "role": "assistant", "content": "done"},
                {"id": "u2", "role": "user", "content": "next"},
            ]
        )
        self.assertEqual(spans, [{"start": 0, "end": 4}, {"start": 4, "end": 5}])

    def test_build_conversation_render_spans_keeps_office_conversion_turn_collapsible(self):
        spans = build_conversation_render_spans(
            [
                {"id": "u0", "role": "user", "content": "普通消息"},
                {
                    "id": "u1",
                    "role": "user",
                    "content": "生成 PPTX",
                    "meta": {"workflow_mode": "office_file_conversion", "office_conversion_target": "pptx"},
                },
                {"id": "a1", "role": "assistant", "content": "working"},
                {"id": "t1", "role": "tool", "tool_call_id": "tool-1", "content": "ok"},
                {"id": "a2", "role": "assistant", "content": "done"},
            ]
        )
        self.assertEqual(spans, [{"start": 0, "end": 1}, {"start": 1, "end": 5}])


if __name__ == "__main__":
    unittest.main()
