import os
import tempfile
import unittest

from core.chat_storage import ChatStorage
from core.message_persistence import (
    filter_persistable_messages,
    project_canonical_messages,
    project_provider_messages,
)


class TestMessagePersistenceProjection(unittest.TestCase):
    def test_valid_tool_round_is_preserved(self):
        messages = [
            {"id": "u1", "role": "user", "content": "read"},
            {
                "id": "a1",
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            },
            {"id": "t1", "role": "tool", "tool_call_id": "call-1", "content": "ok"},
            {"id": "a2", "role": "assistant", "content": "done"},
        ]

        self.assertEqual(filter_persistable_messages(messages), messages)

    def test_broken_tool_round_is_excluded_without_dropping_later_user_message(self):
        messages = [
            {"id": "u1", "role": "user", "content": "old"},
            {
                "id": "a-broken",
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-missing",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": "{}"},
                    }
                ],
            },
            {"id": "u2", "role": "user", "content": "continue normally"},
        ]

        projected, excluded = project_provider_messages(messages)

        self.assertEqual([item["id"] for item in projected], ["u1", "u2"])
        self.assertEqual(excluded, ["a-broken"])
        self.assertEqual([item["id"] for item in messages], ["u1", "a-broken", "u2"])

    def test_closed_parallel_tool_round_accepts_results_in_completion_order(self):
        messages = [
            {
                "id": "assistant-tools",
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call-1", "function": {"name": "one", "arguments": "{}"}},
                    {"id": "call-2", "function": {"name": "two", "arguments": "{}"}},
                ],
            },
            {"id": "tool-2", "role": "tool", "tool_call_id": "call-2", "content": "two"},
            {"id": "tool-1", "role": "tool", "tool_call_id": "call-1", "content": "one"},
        ]

        projected, excluded = project_canonical_messages(messages)

        self.assertEqual(projected, messages)
        self.assertEqual(excluded, [])

    def test_old_broken_sqlite_history_stays_intact_while_new_turn_is_appendable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            storage = ChatStorage(os.path.join(temp_dir, "chat_history.sqlite"))
            broken = [
                {"id": "u1", "role": "user", "content": "old"},
                {
                    "id": "a-broken",
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-missing",
                            "type": "function",
                            "function": {"name": "write_file", "arguments": "{}"},
                        }
                    ],
                },
            ]
            storage.save_conversation("legacy-broken", broken, title="Legacy")
            loaded = storage.get_messages("legacy-broken")
            new_user = {"id": "u2", "role": "user", "content": "continue"}

            storage.save_conversation_safely(
                "legacy-broken",
                loaded + [new_user],
                title="Legacy",
            )

            sqlite_messages = storage.get_messages("legacy-broken")
            self.assertEqual(
                [item["id"] for item in sqlite_messages],
                ["u1", "a-broken", "u2"],
            )
            self.assertEqual(
                [item["id"] for item in project_provider_messages(sqlite_messages)[0]],
                ["u1", "u2"],
            )

    def test_runtime_repair_round_never_enters_persistable_projection(self):
        messages = [
            {
                "id": "a-repair",
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-repair",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
                "meta": {"runtime_repair_only": True},
            },
            {
                "id": "t-repair",
                "role": "tool",
                "tool_call_id": "call-repair",
                "content": "failed",
                "meta": {"runtime_repair_only": True},
            },
            {"id": "a-final", "role": "assistant", "content": "success"},
        ]

        self.assertEqual(
            [item["id"] for item in filter_persistable_messages(messages)],
            ["a-final"],
        )

        current_run_projection, _excluded = project_provider_messages(
            messages,
            include_runtime_repairs=True,
        )
        self.assertEqual(
            [item["id"] for item in current_run_projection],
            ["a-repair", "t-repair", "a-final"],
        )


if __name__ == "__main__":
    unittest.main()
