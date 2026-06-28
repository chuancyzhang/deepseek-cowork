import unittest

from core.conversation_render import build_conversation_render_items, build_conversation_render_spans


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
