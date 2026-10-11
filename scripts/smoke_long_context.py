"""Opt-in live-model continuity check using synthetic data and temporary storage.

Run with --live to use the currently selected model (incurs normal API usage).
The model's local test budget is deliberately 32k to exercise three windows.
No application settings, real conversations, or tool side effects are changed.
"""

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent import LLMWorker
from core.chat_save_queue import ChatSaveRequest, ChatSaveWorker, ContextSaveRequest
from core.chat_storage import ChatStorage
from core.config_manager import ConfigManager
from core.context_window import ContextWindowManager
from core.data_security import begin_run
from core.llm.factory import LLMFactory
from core.long_context import normalize_policy
from core.runtime_journal import RuntimeJournal


def run_live(report_path):
    config = ConfigManager()
    profile = copy.deepcopy(config.get_model_profile() or {})
    if not profile.get("api_key"):
        raise RuntimeError("当前模型没有配置 API Key，未执行真实请求。")
    profile.update(context_window_tokens=32000, max_output_tokens=8192)
    started = time.time()
    usage, validations, summary_requests = [], [], []
    with tempfile.TemporaryDirectory(prefix="cowork-context-smoke-") as directory:
        storage = ChatStorage(os.path.join(directory, "chat_history.sqlite"))
        journal = RuntimeJournal(directory)
        session_id, run_id = "context-smoke", "continuity-run"
        messages = [{"id": "task", "role": "user", "content": (
            "This is a synthetic continuity test. Preserve the invoice amounts, selected color, "
            "and evidence code from the early evidence, applying later corrections. Never send email or execute tools. "
            "When asked for a validation answer, return only JSON with keys total, color, secret. "
            "Do not drop facts needed for that answer.")},
            {"id": "early-evidence", "role": "assistant", "content":
             "Verified synthetic evidence: invoice A=17, B=25, total=42. Selected color=blue. Evidence code=EMBER-42."}]
        storage.save_conversation_safely(session_id, messages)
        journal.begin_run(session_id, run_id, writer_owner="ui:smoke", base_messages=messages,
                          extra={"user_message_id": "task"})
        queue = ChatSaveWorker(storage.db_path, debounce_ms=5000)
        revision = 0
        def emit(event):
            nonlocal revision
            if event.get("type") == "llm_usage":
                usage.append(event["usage"])
            if event.get("type") == "context_rollover":
                print(json.dumps({key: event.get(key) for key in ("phase", "part", "before_tokens", "after_tokens", "error")
                                  if event.get(key) is not None}, ensure_ascii=False), flush=True)
                if event.get("phase") == "failed":
                    worker.is_stopped = True
            if event.get("type") != "context_storage_request":
                return
            if event["operation"] == "prepare":
                known = {m["id"] for m in messages}
                messages.extend(copy.deepcopy(m) for m in event["generated_messages"] if m["id"] not in known)
                revision += 1
                queue.enqueue(ChatSaveRequest(session_id, copy.deepcopy(messages), "Synthetic test", "running", {}, 0, revision))
            queue.enqueue_context(ContextSaveRequest(session_id, run_id, event["operation_id"],
                event["operation"], event["payload"], event["expires_at"]))

        worker = SimpleNamespace(chat_storage=storage, runtime_journal=journal, conversation_id=session_id,
            session_id=session_id, request_id=run_id, turn_id="1", workspace_dir=directory,
            messages=copy.deepcopy(messages), run_context={"selected_model_profile": profile},
            config_manager=config, is_stopped=False, observability_signal=SimpleNamespace(emit=emit))
        worker._record_provider_attempt = lambda aid, data: journal.record_attempt(session_id, aid, data)
        worker._register_provider_stream = lambda stream: not worker.is_stopped
        worker._release_provider_stream = lambda stream: None
        worker._provider_chat_stream = lambda provider, messages, **kwargs: provider.chat_stream(messages, **kwargs)
        def summarize(provider, request, rollover, part, repair):
            summary_requests.append({"part": part, "repair": repair})
            return LLMWorker._generate_context_checkpoint(worker, provider, request, rollover, part, repair)
        worker._generate_context_checkpoint = summarize
        manager = ContextWindowManager(worker, normalize_policy({"enabled": True}))
        worker.context_window_manager = manager
        provider = LLMFactory.create_provider(config, profile.get("id"), model_profile=profile)
        provider.data_security_run = begin_run(config, scope=session_id, run_id=run_id)
        current = [{"role": "system", "content": "Follow the current task, preserve corrected requirements, and return concise JSON when requested."},
                   *copy.deepcopy(messages)]
        generated = []
        queue.start()
        try:
            for window in range(3):
                if window == 1:
                    correction = {"id": "correction", "role": "user", "content": "Correction: revoke blue. The selected color is now teal. All other facts stay the same."}
                    current.append(correction)
                    generated.append(correction)
                if window == 2:
                    followup = {"id": "followup", "role": "user", "content": "Continue the same task; preserve the effective requirements."}
                    current.append(followup)
                    generated.append(followup)
                # Synthetic historical findings; each page has its own stable identity.
                for page in range(9):
                    item = {"id": f"evidence-{window}-{page}", "role": "assistant", "content": (
                        f"Synthetic archived page {window}/{page}. No operations were performed. "
                        + "Neutral observation: invoice evidence was inspected; no additional requirements. " * 120)}
                    current.append(item)
                    generated.append(item)
                recent = {"id": f"recent-{window}", "role": "assistant", "content": "Historical evidence saved. Ready for the next request."}
                current.append(recent)
                generated.append(recent)
                projected, switched = manager.prepare(current, generated, [], provider)
                if not switched:
                    raise AssertionError("Synthetic history did not trigger a window switch")
                query = {"role": "user", "content": "Validation answer now: return only JSON with total, color, secret. Use the effective requirements and earliest evidence."}
                response = []
                for event in provider.chat_stream([*projected, query], tools=[], request_context={
                    "client_request_id": f"context-smoke-validation-{window}", "tools_disabled": True, "max_output_tokens": 8192}):
                    if event.get("type") == "content":
                        response.append(event.get("content") or "")
                    elif event.get("type") == "usage":
                        usage.append({**event["usage"], "purpose": "validation"})
                    elif event.get("type") == "error":
                        raise RuntimeError(event.get("content"))
                text = "".join(response).strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                value = json.loads(text)
                expected = {"total": 42, "color": "blue" if window == 0 else "teal", "secret": "EMBER-42"}
                if value != expected:
                    raise AssertionError(f"Continuity mismatch: {value!r} != {expected!r}")
                validations.append({"window": manager.window["ordinal"], "answer": value})
                print(json.dumps(validations[-1], ensure_ascii=False), flush=True)
            report = {"model": profile.get("model_name"), "protocol": profile.get("api_protocol"),
                      "test_model_capacity": 32000, "target_tokens": 300000,
                      "effective_tokens": manager.budget.effective_tokens,
                      "elapsed_seconds": round(time.time() - started, 1), "windows": validations,
                      "summary_requests": len(summary_requests), "usage": usage,
                      "history_messages": len(storage.get_messages(session_id)),
                      "early_evidence_readable": storage.context_history_read(session_id, "early-evidence")["content"] == messages[1]["content"]}
            destination = Path(report_path).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"success": True, "report": str(destination), "summary_requests": len(summary_requests)}, ensure_ascii=False), flush=True)
        finally:
            queue.stop_worker(3000)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send synthetic requests using the selected model")
    parser.add_argument("--report", default=".tmp/long-context-live.json")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live explicitly to run the billable model check.")
    run_live(args.report)
