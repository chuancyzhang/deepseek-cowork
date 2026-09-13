import copy
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from bootstrap_plugins import resolve
from bootstrap_plugins.deepseek_flash_minimal import DeepSeekFlashMinimalBootstrap
from core.agent import LLMWorker
from core.execution_authorization import ExecutionAuthorization
from core.llm.providers import retry_model_api_stream


PROFILE = {
    "provider_type": "openai", "model_name": "deepseek-flash",
    "base_url": "https://api.deepseek.com", "api_key": "test-key",
    "bootstrap_plugin": "deepseek_flash_minimal",
}
TASK = {
    "id": "user-1", "role": "user", "content": "Inspect the code and finish the task.",
    "content_parts": [{"type": "text", "text": "Inspect the code and finish the task."}],
}


def tool_call(name, call_id="call-1", arguments=None):
    return {
        "type": "tool_call", "id": call_id, "index": 0,
        "function": {"name": name, "arguments": arguments or '{}'},
    }


class ConfigStub:
    def __init__(self, path, profile=None):
        self.path = path
        self.profile = dict(PROFILE if profile is None else profile)

    def get(self, key, default=None):
        return {"api_key": "test-key"}.get(key, default)

    def get_chat_history_dir(self):
        return self.path

    def get_model_profile(self, _model_id=None):
        return self.profile


class SkillStub:
    def __init__(self):
        self.calls = []

    def get_tool_definitions(self, **_kwargs):
        return [{
            "type": "function", "function": {
                "name": "read_file", "description": "Normal Cowork tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

    def get_tools_for_skill(self, _name):
        return []

    def get_skill_of_tool(self, _name):
        return ""

    def get_full_skill_prompt(self, name):
        return f"Selected skill instructions: {name}"

    def get_tool_record(self, _name):
        return {"source_kind": "core_builtin", "read_only": True}

    def call_tool(self, name, args, context=None):
        self.calls.append((name, args))
        return {"status": "success", "content": "normal tool result"}


class ProviderStub:
    provider_name = "OpenAI"
    model_name = "deepseek-flash"
    base_url = "https://api.deepseek.com"
    api_protocol = "chat_completions"
    thinking_enabled = True

    def __init__(self, rounds, protocol="chat_completions"):
        self.rounds = rounds
        self.requests = []
        self.api_protocol = protocol
        self.requires_responses_replay = protocol == "responses"
        self.requires_deepseek_responses_replay = protocol == "responses"

    def chat_stream(self, messages, tools=None, prompt_cache_key=None, request_context=None):
        index = len(self.requests)
        self.requests.append(copy.deepcopy({"messages": messages, "tools": tools}))
        if index >= len(self.rounds):
            raise AssertionError("Unexpected additional model request")
        response = self.rounds[index]
        if callable(response):
            yield from response()
        else:
            if any(chunk.get("type") == "tool_call" for chunk in response):
                yield {"type": "reasoning", "content": "Inspect the task with the available tool."}
            yield from response


class TestAgentBootstrap(unittest.TestCase):
    def run_worker(self, rounds, *, profile=None, context=None, history=None, shell_result=None, prepare_error=None, prompt_error=None, protocol="chat_completions", authorization=None):
        provider = ProviderStub(rounds, protocol)
        skills = SkillStub()
        events, finished = [], []
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("core.agent.SkillManager", return_value=skills),
            patch("core.agent.LLMFactory.create_provider", return_value=provider),
            patch.object(LLMWorker, "_bind_agent_manager"),
            patch.object(LLMWorker, "_build_stable_system_prompt", return_value="NORMAL COWORK", side_effect=prompt_error),
            patch.object(LLMWorker, "_build_runtime_context_prompt", return_value="NORMAL RUNTIME"),
            patch("core.agent.get_runtime_snapshot", return_value={}) as snapshot,
            patch.object(DeepSeekFlashMinimalBootstrap, "prepare", side_effect=prepare_error),
            patch.object(DeepSeekFlashMinimalBootstrap, "call_tool", return_value=shell_result or {
                "status": "success", "content": "bootstrap command result",
            }) as shell,
        ):
            worker = LLMWorker(
                copy.deepcopy(history if history is not None else [TASK]),
                ConfigStub(temp_dir, profile), workspace_dir=temp_dir,
                session_id="session-1", turn_id="turn-1", request_id="request-1",
                run_context=context,
                execution_authorization=authorization or ExecutionAuthorization(
                    "bootstrap-test", "run", True, ""),
            )
            worker.observability_signal.connect(events.append)
            worker.finished_signal.connect(finished.append)
            worker.run()
            self.assertEqual(len(finished), 1)
            return provider.requests, finished[0], events, skills.calls, shell.call_count, snapshot.call_count

    def test_closed_bootstrap_denial_does_not_start_shell_or_change_tool_schema(self):
        rounds = [[tool_call("pwsh", arguments='{"command":"Get-ChildItem"}')],
                  [{"type": "content", "content": "done"}]]
        full_requests, _, _, _, full_calls, _ = self.run_worker(rounds)
        auth = ExecutionAuthorization("session-1", "request-1", False, "artifacts")
        with patch("core.interaction.interaction_service.create_request", return_value={"status": "completed", "approved": False}):
            requests, result, events, _, calls, _ = self.run_worker(rounds, authorization=auth)
        self.assertEqual(full_calls, 1)
        self.assertEqual(calls, 0)
        self.assertEqual(requests[0], full_requests[0])
        self.assertTrue(any(event.get("status") == "denied" for event in events))
        self.assertFalse(result.get("error"), result)

    def test_scope_and_explicit_normal_contexts(self):
        self.assertIsNotNone(resolve(PROFILE, "deepseek-flash"))
        for base_url in (
            "https://api.deepseek.com.evil.test", "https://proxy.test/api.deepseek.com",
            "http://api.deepseek.com", "https://api.deepseek.com:8443", "https://api.deepseek.com/custom",
        ):
            with self.subTest(base_url=base_url):
                self.assertIsNone(resolve({**PROFILE, "base_url": base_url}, "deepseek-flash"))
        normal_cases = [
            {"profile": {**PROFILE, "model_name": name}}
            for name in ("deepseek-v4-flash", "deepseek-pro", "deepseek-flash-custom", "gpt-5")
        ] + [
            {"profile": {**PROFILE, "bootstrap_plugin": ""}},
            {"context": {"workflow_mode": "office_html_first"}},
            {"context": {"knowledge_context": {"refs": [{"knowledge_id": "document-1"}]}}},
        ]
        for options in normal_cases:
            with self.subTest(options=options):
                requests, result, events, _, shell_calls, _ = self.run_worker(
                    [[{"type": "content", "content": "done"}]], **options,
                )
                self.assertNotIn("error", result)
                self.assertEqual(requests[0]["messages"][0]["content"], "NORMAL COWORK")
                self.assertFalse(any(e.get("phase") == "BOOTSTRAP" for e in events))
                self.assertEqual(shell_calls, 0)

    def test_minimal_request_transitions_once_and_history_resumes_normal(self):
        requests, result, events, calls, shell_calls, snapshot_calls = self.run_worker([
            [tool_call("pwsh", arguments='{"command":"Get-ChildItem"}')],
            [tool_call("read_file", "call-2")],
            [{"type": "content", "content": "done"}],
        ])
        self.assertNotIn("error", result)
        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[0]["messages"], [
            {"role": "system", "content": "You are a helpful software engineer assistant."}, TASK,
        ])
        self.assertEqual([t["function"]["name"] for t in requests[0]["tools"]], ["pwsh"])
        self.assertEqual(requests[1]["messages"][0]["content"], "NORMAL COWORK")
        self.assertEqual([t["function"]["name"] for t in requests[1]["tools"]], ["read_file"])
        self.assertTrue(any("bootstrap command result" in str(m.get("content")) for m in requests[1]["messages"]))
        self.assertEqual(calls, [("read_file", {})])
        self.assertEqual((shell_calls, snapshot_calls), (1, 1))
        phases = [e["phase"] for e in events if e["type"] == "bootstrap_phase_changed"]
        self.assertEqual(phases, ["BOOTSTRAP", "NORMAL"])
        self.assertTrue(all(e["ok"] for e in events if e["type"] == "conversation_prefix_check"))
        history = [TASK] + result["generated_messages"] + [{"role": "user", "content": "Continue"}]
        resumed, final, _, _, shell_calls, _ = self.run_worker(
            [[{"type": "content", "content": "continued"}]], history=history,
        )
        self.assertNotIn("error", final)
        self.assertEqual(resumed[0]["messages"][0]["content"], "NORMAL COWORK")
        self.assertEqual(shell_calls, 0)

    def test_text_bootstrap_and_tool_failure_both_continue_normal(self):
        for first, shell_result in (
            ([{"type": "content", "content": "Initial observation"}], None),
            ([tool_call("pwsh", arguments='{"command":"bad-command"}')], {"status": "error", "error": "command failed"}),
            ([tool_call("read_file")], None),
        ):
            with self.subTest(first=first):
                requests, result, _, calls, shell_calls, _ = self.run_worker(
                    [first, [{"type": "content", "content": "done"}]], shell_result=shell_result,
                )
                self.assertNotIn("error", result)
                self.assertEqual(len(requests), 2)
                self.assertEqual(requests[1]["messages"][0]["content"], "NORMAL COWORK")
                self.assertLessEqual(shell_calls, 1)
                self.assertEqual(calls, [])
                self.assertFalse(any((m.get("meta") or {}).get("runtime_repair_only") for m in result["generated_messages"]))

    def test_provider_error_keeps_partial_output_and_falls_back_only_once(self):
        for second in (
            [{"type": "content", "content": "recovered"}],
            [{"type": "error", "content": "normal failed"}],
        ):
            with self.subTest(second=second):
                requests, result, events, _, shell_calls, _ = self.run_worker([
                    [{"type": "content", "content": "Partial observation"},
                     tool_call("pwsh", arguments='{"command":"partial'),
                     {"type": "error", "content": "bootstrap failed"}],
                    second,
                ])
                self.assertEqual(len(requests), 2)
                self.assertEqual(shell_calls, 0)
                partials = [m for m in requests[1]["messages"] if m.get("content") == "Partial observation"]
                self.assertEqual(len(partials), 1)
                self.assertNotIn("tool_calls", partials[0])
                self.assertEqual(len([e for e in events if e["type"] == "bootstrap_phase_changed"]), 2)
                self.assertEqual("error" in result, second[0]["type"] == "error")

    def test_bootstrap_provider_retry_immediately_hands_off_and_missing_shell_skips(self):
        attempts = []

        def fail(attempt):
            attempts.append(attempt)
            raise ConnectionError("connection reset")
            yield

        with patch("core.llm.providers._wait_before_model_retry") as wait:
            requests, result, _, _, _, _ = self.run_worker([
                lambda: retry_model_api_stream(fail), [{"type": "content", "content": "done"}],
            ])
            self.assertNotIn("error", result)
            self.assertEqual(len(requests), 2)
            self.assertEqual(attempts, [0])
            wait.assert_not_called()
        requests, result, events, _, _, _ = self.run_worker(
            [[{"type": "content", "content": "done"}]], prepare_error=FileNotFoundError("pwsh missing"),
        )
        self.assertNotIn("error", result)
        self.assertEqual(requests[0]["messages"][0]["content"], "NORMAL COWORK")
        self.assertEqual(next(e for e in events if e["type"] == "bootstrap_phase_changed")["reason"], "initialization_failed")

    def test_pwsh_unicode_exit_code_and_pre_execution_stop(self):
        plugin = DeepSeekFlashMinimalBootstrap()
        plugin.prepare()
        # A real local shell smoke; replace only the environment provisioning layer.
        def launch(args, **kwargs):
            return subprocess.Popen(
                args, cwd=kwargs["cwd"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        with tempfile.TemporaryDirectory() as workspace, patch(
            "bootstrap_plugins.deepseek_flash_minimal.run_in_sandbox", side_effect=launch,
        ) as launch_mock:
            for command, code, content in (("Write-Output '启动正常'", 0, "启动正常"), ("Write-Output 'retained'; exit 7", 7, "retained")):
                result = plugin.call_tool("pwsh", {"command": command}, {"workspace_dir": workspace})
                self.assertEqual(result["exit_code"], code)
                self.assertIn(content, result["content"])
                self.assertEqual("error" in result, code != 0)
            result = plugin.call_tool("pwsh", {"command": "Write-Output 'skip'"}, {"workspace_dir": workspace, "abort_check": lambda: True})
            self.assertEqual(result["status"], "denied")
            self.assertEqual(launch_mock.call_count, 2)

    def test_normal_prompt_failure_preserves_completed_bootstrap_result(self):
        requests, result, _, _, shell_calls, _ = self.run_worker(
            [[tool_call("pwsh", arguments='{"command":"Get-ChildItem"}')]],
            prompt_error=PermissionError("workspace instructions unreadable"),
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(shell_calls, 1)
        self.assertIn("error", result)
        self.assertTrue(any(
            message.get("role") == "tool" and "bootstrap command result" in message["content"]
            for message in result["generated_messages"]
        ))

    def test_responses_desktop_text_with_selected_skills_restores_full_context(self):
        args = '{"command":"Get-ChildItem"}'
        first_replay = [
            {"id": "rs_1", "type": "reasoning", "summary": [],
             "content": [{"type": "reasoning_text", "text": "Inspect the task with the available tool."}]},
            {"id": "fc_1", "type": "function_call", "call_id": "call-1", "name": "pwsh", "arguments": args},
        ]
        requests, result, _, _, shell_calls, _ = self.run_worker([
            [tool_call("pwsh", arguments=args), {"type": "response_items", "items": first_replay}],
            [{"type": "content", "content": "done"}, {"type": "response_items", "items": [
                {"id": "msg_2", "type": "message", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": "done", "annotations": []}]},
            ]}],
        ], protocol="responses", context={
            "selected_skill_names": ["web"],
            "knowledge_context": {"connection_id": "connected", "refs": [], "session_id": "session-1"},
        })
        self.assertNotIn("error", result)
        self.assertEqual(shell_calls, 1)
        self.assertEqual(len(requests[0]["messages"]), 2)
        self.assertEqual([tool["function"]["name"] for tool in requests[0]["tools"]], ["pwsh"])
        self.assertTrue(any("Selected skill instructions: web" in str(m.get("content")) for m in requests[1]["messages"]))
        self.assertTrue(any("本次任务资料库上下文" in str(m.get("content")) for m in requests[1]["messages"]))
        replayed = next(m for m in requests[1]["messages"] if m.get("tool_calls"))
        self.assertEqual(replayed["_deepseek_responses_replay_items"], first_replay)


if __name__ == "__main__":
    unittest.main()
