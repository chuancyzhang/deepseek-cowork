import sys
import subprocess
import tempfile
import os
import ast
import re
import json
import platform
import time
import shutil
import uuid
import threading
import hashlib
from datetime import datetime
from PySide6.QtCore import QThread, Signal, Slot, QObject, QMutex, QWaitCondition
from core.skill_manager import SkillManager
from core.env_utils import get_python_executable, get_python_runtime_snapshot, get_runtime_snapshot
from core.sandbox_runtime import get_runtime_executable, run_in_sandbox
from core.llm.factory import LLMFactory
from core.chat_storage import ChatStorage
from core.agent_manager import AGENT_MANAGEMENT_TOOLS, get_agent_manager_registry
from core.clarify_mode import (
    get_clarifying_read_tools,
    RUN_MODE_EXECUTION,
    RUN_MODE_CLARIFYING,
    is_tool_allowed_in_clarifying,
    json_copy,
    normalize_selected_skill_names,
    normalize_run_context,
)
from core.sop_manager import build_sop_prompt_fragment
from core.llm.deepseek import is_deepseek_request

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

class SecurityError(Exception):
    pass


_OPENAI_PROTOCOL_LOCK = threading.Lock()


def _needs_openai_protocol_lock(provider):
    return getattr(provider, "protocol_family", "") == "openai-compatible"


def _acquire_openai_protocol_lock(worker):
    waited = False
    while not worker.is_stopped:
        if _OPENAI_PROTOCOL_LOCK.acquire(timeout=0.1):
            return waited
        waited = True
    return None


def _stable_json_hash(value):
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]

def validate_code_safety(code, allowed_dir, god_mode=False):
    """AST 静态分析代码安全性"""
    if god_mode:
        return True

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SecurityError(f"Syntax Error: {e}")

    allowed_dir = os.path.abspath(allowed_dir).lower()

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if ".." in val:
                 raise SecurityError(f"Security Alert: Path traversal '..' detected in string: '{val}'")
            if os.path.isabs(val):
                abs_val = os.path.abspath(val).lower()
                if not abs_val.startswith(allowed_dir):
                     raise SecurityError(f"Security Alert: Unauthorized absolute path access: '{val}'")
    return True

class CodeWorker(QThread):
    """后台执行 Python 代码的线程"""
    output_signal = Signal(str)
    finished_signal = Signal()
    input_request_signal = Signal(str)

    def __init__(self, code, cwd, god_mode=False):
        super().__init__()
        self.code = code
        self.cwd = cwd
        self.god_mode = god_mode
        self.process = None
        self.is_stopped = False

    def provide_input(self, text):
        """Write user input to stdin"""
        if self.process and self.process.stdin:
            try:
                self.process.stdin.write(text + "\n")
                self.process.stdin.flush()
            except Exception as e:
                print(f"Error writing to stdin: {e}")

    def stop(self):
        self.is_stopped = True
        if self.process:
            try:
                self.process.terminate() # Try graceful termination
                self.output_signal.emit("System: Terminating process...")
            except:
                pass

    def run(self):
        temp_path = None
        try:
            # 1. Validation
            try:
                validate_code_safety(self.code, self.cwd, god_mode=self.god_mode)
            except SecurityError as e:
                self.output_signal.emit(f"❌ {str(e)}")
                # We will let the finally block emit finished_signal
                return

            # Prepend input() override to capture user interaction
            input_override = """
import sys
import io

# Set stdout/stderr to utf-8 explicitly for Windows console
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

def input(prompt=""):
    print(f"__REQUEST_INPUT__:{prompt}", flush=True)
    return sys.stdin.readline().strip()
"""
            full_code = input_override + "\n" + self.code

            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
                f.write(full_code)
                temp_path = f.name

            # Determine python executable
            python_exe = get_runtime_executable("python")
            if not python_exe:
                self.output_signal.emit("Execution Error: Sandbox Python runtime is missing.")
                return

            if self.is_stopped: return

            self.output_signal.emit(f"Running with {python_exe} in: {self.cwd}...")
            self.process = run_in_sandbox(
                [python_exe, "-X", "utf8", temp_path],
                cwd=self.cwd,
                skill_id="python-runner",
                shell_kind="exec",
                text=True,
            )
            
            # Real-time output reading
            while True:
                if self.is_stopped:
                    self.process.kill()
                    self.output_signal.emit("⚠️ Process stopped by user.")
                    break
                
                output = self.process.stdout.readline()
                if output == '' and self.process.poll() is not None:
                    break
                if output:
                    output = output.strip()
                    if output.startswith("__REQUEST_INPUT__:"):
                        prompt = output.split(":", 1)[1]
                        self.input_request_signal.emit(prompt)
                    else:
                        self.output_signal.emit(output)
            
            if not self.is_stopped:
                stderr = self.process.stderr.read()
                if stderr:
                    self.output_signal.emit(f"Error Output:\n{stderr}")
            
        except Exception as e:
            self.output_signal.emit(f"Execution Error: {e}")
            # Also print to console for debugging
            import traceback
            traceback.print_exc()
            
        finally:
            # Clean up temp file
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except:
                    pass
            self.finished_signal.emit()

def _reasoning_text_from_message(msg):
    if not isinstance(msg, dict):
        return ""
    reasoning_content = msg.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content:
        return reasoning_content
    reasoning = msg.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        return reasoning
    return ""


def _clean_reasoning_content_by_turn(messages, drop_meta=False):
    """
    Drop stale reasoning for ordinary turns, but preserve assistant reasoning for
    every assistant message in a user turn that contains tool calls. DeepSeek's
    thinking mode requires those reasoning_content values to be replayed.
    """
    cleaned = []
    turn_indices = []
    turn_has_tool_calls = False

    def finish_turn():
        nonlocal turn_indices, turn_has_tool_calls
        for idx in turn_indices:
            msg = cleaned[idx]
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role == "assistant" and turn_has_tool_calls:
                reasoning_text = _reasoning_text_from_message(msg)
                if reasoning_text:
                    msg["reasoning_content"] = reasoning_text
                else:
                    msg.pop("reasoning_content", None)
            else:
                msg.pop("reasoning_content", None)
            msg.pop("reasoning", None)
            if drop_meta:
                msg.pop("meta", None)
        turn_indices = []
        turn_has_tool_calls = False

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "user":
            finish_turn()
        clean_msg = msg.copy()
        cleaned.append(clean_msg)
        turn_indices.append(len(cleaned) - 1)
        if clean_msg.get("role") == "assistant" and clean_msg.get("tool_calls"):
            turn_has_tool_calls = True

    finish_turn()
    return cleaned


def clear_reasoning_content(messages):
    return _clean_reasoning_content_by_turn(messages)

def repair_tool_call_sequence(messages):
    cleaned = []
    pending = {}

    def drop_pending():
        nonlocal pending, cleaned
        if not pending:
            return
        grouped = {}
        for call_id, idx in pending.items():
            grouped.setdefault(idx, set()).add(call_id)
        for idx, ids in grouped.items():
            if idx < 0 or idx >= len(cleaned):
                continue
            msg = cleaned[idx]
            calls = msg.get("tool_calls") or []
            kept = [tc for tc in calls if tc.get("id") not in ids]
            if kept:
                msg["tool_calls"] = kept
            else:
                msg.pop("tool_calls", None)
            cleaned[idx] = msg
        pending = {}

    for msg in messages:
        if not isinstance(msg, dict):
            continue

        role = msg.get("role")

        if role != "tool" and pending:
            drop_pending()

        if role == "assistant" and msg.get("tool_calls"):
            normalized_calls = []
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                call_id = tc.get("id")
                func = tc.get("function")
                if not call_id or not isinstance(func, dict) or not func.get("name"):
                    continue
                args = func.get("arguments", "")
                if not isinstance(args, str):
                    try:
                        args = json.dumps(args, ensure_ascii=False)
                    except Exception:
                        args = str(args)
                normalized_calls.append(
                    {
                        "id": call_id,
                        "type": tc.get("type", "function"),
                        "function": {
                            "name": func.get("name"),
                            "arguments": args
                        }
                    }
                )
            msg_copy = msg.copy()
            if normalized_calls:
                msg_copy["tool_calls"] = normalized_calls
                cleaned.append(msg_copy)
                idx = len(cleaned) - 1
                for tc in normalized_calls:
                    pending[tc["id"]] = idx
            else:
                msg_copy.pop("tool_calls", None)
                cleaned.append(msg_copy)
            continue

        if role == "tool":
            call_id = msg.get("tool_call_id")
            if call_id and call_id in pending:
                cleaned.append(msg.copy())
                pending.pop(call_id, None)
            continue

        cleaned.append(msg.copy())

    if pending:
        drop_pending()

    return cleaned

def drop_invalid_tool_call_rounds_without_reasoning(messages):
    cleaned = []
    dropped_rounds = []

    def process_turn(turn, start_index):
        assistant_indices = []
        tool_call_ids = set()
        has_tool_calls = False
        missing_reasoning = False

        for offset, item in enumerate(turn):
            if not isinstance(item, dict):
                continue
            if item.get("role") == "assistant":
                if item.get("tool_calls"):
                    has_tool_calls = True
                    for tool_call in item.get("tool_calls") or []:
                        if isinstance(tool_call, dict) and tool_call.get("id"):
                            tool_call_ids.add(tool_call["id"])
                assistant_indices.append(offset)

        if has_tool_calls:
            for offset in assistant_indices:
                if not _reasoning_text_from_message(turn[offset]):
                    missing_reasoning = True
                    break

        if not has_tool_calls or not missing_reasoning:
            return [item.copy() for item in turn if isinstance(item, dict)], None

        pruned = []
        kept_assistant_count = 0
        for item in turn:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role == "tool" and item.get("tool_call_id") in tool_call_ids:
                continue
            if role == "assistant" and item.get("tool_calls"):
                continue
            msg_copy = item.copy()
            if role == "assistant":
                msg_copy.pop("reasoning_content", None)
                msg_copy.pop("reasoning", None)
                kept_assistant_count += 1
            pruned.append(msg_copy)

        return pruned, {
            "turn_start_index": start_index,
            "tool_call_ids": sorted(tool_call_ids),
            "kept_assistant_count": kept_assistant_count,
        }

    turn = []
    turn_start_index = 0
    for index, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "user" and turn:
            processed, dropped = process_turn(turn, turn_start_index)
            cleaned.extend(processed)
            if dropped:
                dropped_rounds.append(dropped)
            turn = []
            turn_start_index = index
        elif not turn:
            turn_start_index = index
        turn.append(msg)

    if turn:
        processed, dropped = process_turn(turn, turn_start_index)
        cleaned.extend(processed)
        if dropped:
            dropped_rounds.append(dropped)

    return cleaned, dropped_rounds

def sanitize_llm_messages(messages, require_reasoning_replay=False, return_metadata=False):
    repaired = repair_tool_call_sequence(messages)
    dropped_rounds = []
    if require_reasoning_replay:
        repaired, dropped_rounds = drop_invalid_tool_call_rounds_without_reasoning(repaired)
    cleaned = _clean_reasoning_content_by_turn(repaired, drop_meta=True)
    if return_metadata:
        return cleaned, {"dropped_incomplete_reasoning_rounds": dropped_rounds}
    return cleaned

class LLMWorker(QThread):
    """后台调用 LLM API 的线程，支持 Tool Calls 和多轮思考"""
    finished_signal = Signal(dict)
    step_signal = Signal(str) # 用于输出中间步骤日志
    thinking_signal = Signal(str) # 用于实时输出思考过程
    skill_used_signal = Signal(str) # Signal to report active skill usage
    tool_call_signal = Signal(dict)
    tool_result_signal = Signal(dict)
    content_signal = Signal(str)
    output_signal = Signal(str) # For generic output/errors
    agent_state_signal = Signal(dict) # Signal to report sub-agent status
    observability_signal = Signal(dict)
    abort_signal = Signal() # Signal emitted when the worker is stopped

    def __init__(
        self,
        messages,
        config_manager,
        workspace_dir=None,
        automation_runner=None,
        parent_agent_id=None,
        session_id=None,
        conversation_id=None,
        agent_id=None,
        is_subagent=False,
        run_context=None,
        turn_id=None,
    ):
        super().__init__()
        self.messages = messages
        self.config_manager = config_manager
        self.api_key = config_manager.get("api_key")
        self.workspace_dir = workspace_dir
        self.automation_runner = automation_runner
        self.parent_agent_id = parent_agent_id
        self.session_id = session_id or ""
        self.conversation_id = conversation_id or self.session_id
        self.agent_id = agent_id or parent_agent_id or ""
        self.is_subagent = bool(is_subagent or parent_agent_id)
        self.run_context = normalize_run_context(run_context)
        self.turn_id = str(turn_id or "")
        
        # Flags for control
        self.is_paused = False
        self.is_stopped = False
        self._guidance_lock = threading.Lock()
        self._pending_guidance = []
        self._guidance_open = True
        
        # Initialize Skill Manager
        self.skill_manager = SkillManager(workspace_dir, config_manager)
        self.discovered_tool_names = set()
        self.tools = []
        self._refresh_tool_definitions()
        # Per-run filesystem read/write state used by filesystem tools.
        self.file_state_cache = {"reads": {}}
        self.chat_storage = None
        self.agent_manager = None
        try:
            history_dir = self.config_manager.get_chat_history_dir()
            db_path = os.path.join(history_dir, "chat_history.sqlite")
            self.chat_storage = ChatStorage(db_path)
        except Exception:
            self.chat_storage = None
        self._bind_agent_manager()
        self._prompt_context_date = datetime.now().strftime("%Y-%m-%d")
        self._stable_system_prompt = None

    def _selected_skill_names(self):
        return normalize_selected_skill_names(self.run_context.get("selected_skill_names"))

    def _allowed_skill_names(self):
        return normalize_selected_skill_names(self.run_context.get("allowed_skill_names"))

    def _selected_skill_tool_names(self):
        tool_names = []
        seen = set()
        if not hasattr(self.skill_manager, "get_tools_for_skill"):
            return tool_names
        for skill_name in self._selected_skill_names():
            for tool_name in self.skill_manager.get_tools_for_skill(skill_name):
                text = str(tool_name or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                tool_names.append(text)
        return tool_names

    def _refresh_tool_definitions(self):
        for tool_name in self._selected_skill_tool_names():
            self.discovered_tool_names.add(tool_name)
        try:
            tools = self.skill_manager.get_tool_definitions(
                run_mode=self._current_run_mode(),
                discovered_tool_names=self.discovered_tool_names,
                run_context=self.run_context,
            )
        except TypeError:
            try:
                tools = self.skill_manager.get_tool_definitions(
                    run_mode=self._current_run_mode(),
                    discovered_tool_names=self.discovered_tool_names,
                )
            except TypeError:
                tools = self.skill_manager.get_tool_definitions()
        filtered = []
        for item in tools:
            func = item.get("function") if isinstance(item, dict) else None
            name = func.get("name") if isinstance(func, dict) else ""
            if self.is_subagent and name in AGENT_MANAGEMENT_TOOLS:
                continue
            if name and hasattr(self.skill_manager, "is_tool_allowed"):
                try:
                    if not self.skill_manager.is_tool_allowed(name, self._current_run_mode()):
                        continue
                except Exception:
                    pass
            if name and hasattr(self.skill_manager, "is_tool_visible"):
                try:
                    if not self.skill_manager.is_tool_visible(
                        name,
                        self._current_run_mode(),
                        self.discovered_tool_names,
                        run_context=self.run_context,
                    ):
                        continue
                except TypeError:
                    try:
                        if not self.skill_manager.is_tool_visible(
                            name,
                            self._current_run_mode(),
                            self.discovered_tool_names,
                        ):
                            continue
                    except Exception:
                        pass
                except Exception:
                    pass
            if (
                self._is_clarifying_mode()
                and name
                and not hasattr(self.skill_manager, "is_tool_allowed")
                and not is_tool_allowed_in_clarifying(name)
            ):
                continue
            filtered.append(item)
        self.tools = filtered

    def _current_run_mode(self):
        return self.run_context.get("mode") or RUN_MODE_EXECUTION

    def _is_clarifying_mode(self):
        return self._current_run_mode() == RUN_MODE_CLARIFYING

    def _is_tool_allowed_for_mode(self, name):
        if hasattr(self.skill_manager, "is_tool_allowed"):
            try:
                return self.skill_manager.is_tool_allowed(name, self._current_run_mode())
            except Exception:
                return False
        if self._is_clarifying_mode():
            return is_tool_allowed_in_clarifying(name)
        return True

    def _is_tool_visible_for_run(self, name):
        if hasattr(self.skill_manager, "is_tool_visible"):
            try:
                return self.skill_manager.is_tool_visible(
                    name,
                    self._current_run_mode(),
                    self.discovered_tool_names,
                    run_context=self.run_context,
                )
            except TypeError:
                try:
                    return self.skill_manager.is_tool_visible(
                        name,
                        self._current_run_mode(),
                        self.discovered_tool_names,
                    )
                except Exception:
                    return True
            except Exception:
                return True
        return True

    def _current_turn_has_image_input(self, messages):
        for msg in reversed(messages or []):
            if not isinstance(msg, dict):
                continue
            if msg.get("role") != "user":
                continue
            for part in msg.get("content_parts") or []:
                if (
                    isinstance(part, dict)
                    and str(part.get("type") or "").strip().lower() == "input_image"
                ):
                    return True
            return False
        return False

    def _tools_for_messages(self, messages):
        return list(self.tools or [])

    def _bind_agent_manager(self):
        if self.is_subagent:
            self.agent_manager = None
            return
        if not (self.chat_storage and self.config_manager and self.conversation_id):
            self.agent_manager = None
            return
        try:
            registry = get_agent_manager_registry()
            self.agent_manager = registry.get_session_manager(
                self.conversation_id,
                chat_storage=self.chat_storage,
                config_manager=self.config_manager,
                workspace_dir=self.workspace_dir,
                step_signal=self.step_signal,
                agent_state_signal=self.agent_state_signal,
                owner_worker=self,
            )
        except Exception:
            self.agent_manager = None

    @Slot(str)
    def relay_agent_step(self, text):
        try:
            self.step_signal.emit(str(text))
        except Exception:
            pass

    @Slot(dict)
    def relay_agent_state(self, payload):
        try:
            if isinstance(payload, dict):
                self.agent_state_signal.emit(payload)
        except Exception:
            pass

    def pause(self):
        self.is_paused = True
        self.step_signal.emit("System: Paused.")

    def resume(self):
        self.is_paused = False
        self.step_signal.emit("System: Resumed.")

    def stop(self):
        self.is_stopped = True
        self.is_paused = False # Ensure loop breaks if paused
        self.step_signal.emit("System: Stopping...")
        self.abort_signal.emit()

    def steer(self, message, expected_turn_id=None):
        """Queue a user message for the next safe model-request boundary."""
        expected = str(expected_turn_id or "")
        if expected and expected != self.turn_id:
            return {
                "accepted": False,
                "error": "turn_mismatch",
                "expected_turn_id": expected,
                "turn_id": self.turn_id,
            }
        if not isinstance(message, dict):
            return {"accepted": False, "error": "invalid_message", "turn_id": self.turn_id}
        guidance = json_copy(message, {})
        guidance["role"] = "user"
        has_content = bool(str(guidance.get("content") or "").strip())
        has_parts = bool(guidance.get("content_parts"))
        if not has_content and not has_parts:
            return {"accepted": False, "error": "empty_input", "turn_id": self.turn_id}
        with self._guidance_lock:
            if not self._guidance_open or self.is_stopped:
                return {"accepted": False, "error": "turn_not_active", "turn_id": self.turn_id}
            self._pending_guidance.append(guidance)
        self.step_signal.emit("System: Guidance queued for the active turn.")
        return {"accepted": True, "turn_id": self.turn_id}

    def _take_pending_guidance(self, close=False, close_if_empty=False):
        with self._guidance_lock:
            if close or (close_if_empty and not self._pending_guidance):
                self._guidance_open = False
            pending = self._pending_guidance
            self._pending_guidance = []
        return pending

    def _append_pending_guidance(self, current_messages, generated_messages, close=False, close_if_empty=False):
        pending = self._take_pending_guidance(close=close, close_if_empty=close_if_empty)
        if not pending:
            return False
        for message in pending:
            current_messages.append(message)
            generated_messages.append(message)
            self.observability_signal.emit({
                "type": "guidance",
                "content": message.get("content") or "",
                "message_id": message.get("id") or "",
                "timestamp": time.time(),
            })
        self.step_signal.emit(f"System: Applied {len(pending)} guidance message(s).")
        return True

    def _skill_context_hash(self, content):
        return hashlib.sha256(str(content or "").encode("utf-8")).hexdigest()

    def _existing_skill_context_hashes(self, current_messages, skill_name):
        hashes = set()
        for msg in current_messages or []:
            if not isinstance(msg, dict):
                continue
            meta = msg.get("meta") if isinstance(msg.get("meta"), dict) else {}
            if meta.get("kind") not in ("skill_context", "skill_context_update"):
                continue
            if str(meta.get("skill_name") or "") != str(skill_name or ""):
                continue
            content_hash = str(meta.get("content_hash") or "")
            if content_hash:
                hashes.add(content_hash)
        return hashes

    def _build_skill_context_message(self, skill_name, content, source):
        content_hash = self._skill_context_hash(content)
        return {
            "role": "system",
            "content": content,
            "meta": {
                "kind": "skill_context",
                "hidden": True,
                "skill_name": str(skill_name or ""),
                "source": source,
                "content_hash": content_hash,
            },
        }

    def _append_skill_prompts_for_names(self, skill_names, current_messages, disclosed_skills, generated_messages=None, source="skill_prompt"):
        appended = []
        for skill_name in skill_names or []:
            skill_name = str(skill_name or "").strip()
            if not skill_name:
                continue
            prompt_getter = getattr(self.skill_manager, "get_full_skill_prompt", None)
            if not callable(prompt_getter):
                continue
            prompt = prompt_getter(skill_name)
            if not prompt:
                continue
            content_hash = self._skill_context_hash(prompt)
            disclosure_key = f"{skill_name}:{content_hash}"
            if disclosure_key in disclosed_skills:
                continue
            disclosed_skills.add(disclosure_key)
            if content_hash in self._existing_skill_context_hashes(current_messages, skill_name):
                continue
            message = self._build_skill_context_message(skill_name, prompt, source)
            current_messages.append(message)
            if isinstance(generated_messages, list):
                generated_messages.append(message.copy())
            appended.append(message)
        if appended:
            content = "\n\n".join(msg.get("content") or "" for msg in appended)
            self.observability_signal.emit({
                "type": "system_prompt_append",
                "content": content,
                "source": source,
                "kind": "skill_context",
                "skill_names": [msg.get("meta", {}).get("skill_name") for msg in appended],
                "timestamp": time.time(),
            })

    def _append_skill_prompts(self, tool_calls, current_messages, disclosed_skills, generated_messages=None):
        skill_names = []
        for tool in tool_calls or []:
            skill_name = self.skill_manager.get_skill_of_tool(tool.function.name)
            if skill_name:
                skill_names.append(skill_name)
        self._append_skill_prompts_for_names(skill_names, current_messages, disclosed_skills, generated_messages, source="skill_prompt")

    def _build_skill_query(self, messages):
        parts = []
        for msg in messages[-8:]:
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    parts.append(content.strip())
        return "\n".join(parts)

    def _append_tool_search_skill_prompts(self, result_obj, current_messages, disclosed_skills, generated_messages=None):
        if not isinstance(result_obj, dict):
            return
        skill_names = []
        for item in result_obj.get("skills") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("prompt_level") or "").strip().lower() != "full":
                continue
            skill_name = str(item.get("name") or item.get("preferred_skill_name") or "").strip()
            if skill_name:
                skill_names.append(skill_name)
        self._append_skill_prompts_for_names(skill_names, current_messages, disclosed_skills, generated_messages, source="skill_prompt_tool_search")

    def _available_tool_names(self):
        names = []
        for item in self.tools or []:
            function = item.get("function") if isinstance(item, dict) else None
            name = function.get("name") if isinstance(function, dict) else ""
            if name:
                names.append(name)
        return list(dict.fromkeys(names))

    def _build_stable_system_prompt(self):
        enterprise_channels = {"feishu", "dingtalk", "wecom"}
        enterprise_delivery_enabled = (
            (self.run_context.get("im_provider") or "").strip().lower() in enterprise_channels
            or (self.run_context.get("channel") or "").strip().lower() in enterprise_channels
        )

        stable_policy_lines = [
            "注意: 你正在指定的工作区内操作。除非明确允许使用绝对路径，否则所有文件操作都应相对于当前工作区。",
            "能力: 你可以使用 'create_new_skill' 创建新的技能/工具。",
            "Imported / agent script skill 规则: 如果你已经命中了包含 `script_entries` 的 imported/agent skill，优先调用 `run_skill_script`，不要再用 `glob`、`grep` 或 `bash` 去定位该 skill 目录或猜脚本路径。",
            "命令策略: 推荐的通用执行工具是 'run_python_code'、'run_node_code' 和 'bash'，其中优先使用最贴近任务语言的专用执行工具。",
            "1. 可用 Python 完成的数据处理、批量文本处理、脚本化检查、计算和轻量文件分析，优先使用 'run_python_code'。",
            "2. 可用 JavaScript/Node.js 完成的验证、JSON 处理、前端脚本和轻量代码执行，优先使用 'run_node_code'。",
            "3. 需要真实 shell 环境、项目命令、构建测试、git/npm/npx/bash 管道或现有 CLI 时，再使用 'bash'。",
            "4. Windows 打包版的 'bash' 优先使用 Git Bash；若 Git Bash 缺失，执行层会退回 cmd.exe，因此可继续执行 cmd 兼容命令。",
            "5. 避免为了运行内联 Python/JavaScript 而套一层 'bash'；除非必须复用命令行入口，否则直接使用对应专用工具。",
            "能力分层: 核心内置能力只包含始终启用的基础能力；浏览器自动化、网页搜索、金融数据、视频下载、Office/PDF 读取等随包能力位于 ai_skills，是默认关闭的可选插件。",
            "可选插件策略: ai_skills 中的随包插件不会因为随应用分发而自动可调用；需要相关能力时先用 'tool_search' 发现，启用或被本轮工具清单暴露后才能调用。",
            "文件策略: 'workspace_list_files' 只列工作区路径；'text_file_read'、'text_file_write'、'text_file_update' 只处理普通文本文件，不解析或生成 DOCX/PPTX/XLSX/XLS/PDF。",
            "Office/PDF 策略: 读取 DOCX/PPTX/XLSX/XLS/PDF 需先启用可选 document-reader 插件并使用 'document_read'；写入这些格式没有固定工具，应使用 'run_python_code' 和任务所需库自行生成。",
            "旧文件工具名称策略: 不要假设存在 'file_list'、'file_read'、'file_write'、'file_update'、'file_rename'、'file_delete' 等通用文件工具；以当前可用工具清单中的真实名称和边界为准。",
            "判定策略: 不要仅依赖系统 PATH 或常见安装目录猜测 Node/Python 可用性，应先在沙盒中直接执行版本命令验证。",
            "",
            "策略 [技能创建]:",
            "1. 鼓励创建新技能来封装可复用的任务（例如：特定的文件处理、复杂计算、数据转换、系统操作等）。",
            "2. 当你发现某个任务可能在未来被再次使用，或者通过代码实现比通过纯文本生成更可靠时，请果断创建技能。",
            "3. 不要受到过度限制，灵活运用技能来增强你的能力。",
            "",
            "策略 [自我进化]:",
            "1. 你拥有 'update_experience' 工具，用于记录重要的经验教训、配置偏好或特定的工具使用技巧。",
            "2. 当你成功解决一个难题、发现某个工具的最佳实践或遇到并修复了错误时，请务必使用 'update_experience' 记录下来。",
            "3. 这些经验将在未来类似场景中自动注入，帮助你变得更聪明。",
            "",
            "策略 [记忆]:",
            "1. 你拥有 'read_memories' 与 'write_memories' 工具，用于读取/更新 memories.md（可能不存在或为空）。",
            "2. 在每次对话结束后，若出现长期稳定偏好、重要背景、持续项目约定、用户身份/环境信息，才更新 memories.md；否则不要更新。",
            "3. 避免写入敏感信息或临时细节；默认追加，只有在需要整体整理时才使用替换模式。",
            "",
            "策略 [历史检索]: 当用户需要回忆之前讨论内容时，优先使用 'query_history' 工具进行检索。",
            "",
            "策略 [交互]: 如果你需要向用户获取确认，请使用 'request_user_approval'。如果你需要向用户提问、收集文本或选项，请使用 'request_user_input'。",
            "不要在文本回复中直接提问。文本回复仅用于展示推理过程和最终答案。",
            "",
            "策略 [并行工具]: 当需要并行读取多个文件、并行执行 grep/glob、或同时查询多个彼此独立的只读数据源时，优先使用 'parallel_tools'。",
            "策略 [并行工具]: 'parallel_tools' 只适用于彼此独立的只读工具调用；涉及写文件、命令执行、审批、用户输入、经验更新、子代理管理时，保持普通单工具调用。",
            "",
            "策略 [元工具导航]:",
            "1. 工具发现: 需要额外能力时先用 'tool_search'，匹配到的延迟工具会在下一轮可用。",
            "2. 并行只读: 多个独立只读调用可使用 'parallel_tools' 合并为一次显式并发执行。",
            "3. 通用执行: 优先使用 'run_python_code' 或 'run_node_code'，需要 shell/CLI 时使用 'bash'。",
            "4. 技能创建/维护: 使用 'create_new_skill'、'update_skill'、'convert_claude_skill'、'convert_openclaw_skill'、'convert_external_skill'、'analyze_skill_source_folder'、'generate_skill_from_folder'、'run_skill_script'。",
            "5. 经验/记忆/历史: 使用 'update_experience'、'read_memories'、'write_memories'、'query_history'、'query_history_vector'。",
            "6. 用户交互: 使用 'request_user_input'、'request_user_approval'。",
            "7. 多代理协作: 使用 'spawn_agent'、'send_input'、'wait_agent'、'close_agent'、'list_agents'。",
            "8. 元工具导航只是推荐；必须遵守当前可用工具清单、延迟发现机制和当前运行模式权限。",
            "",
            "策略 [思考规范]:",
            "1. 你的思考过程 (Reasoning) 仅用于分析问题、规划步骤和反思结果。",
            "2. 严禁将最终给用户的回复（如任务总结、文件列表、结果汇报）放在思考过程中。",
            "3. 思考过程对用户是折叠的，用户主要阅读的是你的最终 Content 回复。",
        ]
        if enterprise_delivery_enabled:
            stable_policy_lines.insert(
                stable_policy_lines.index("策略 [元工具导航]:"),
                "企业消息会话中，若需要交付文件、图片或链接，请使用 'publish_artifacts'。",
            )
            stable_policy_lines.insert(
                stable_policy_lines.index("7. 多代理协作: 使用 'spawn_agent'、'send_input'、'wait_agent'、'close_agent'、'list_agents'。"),
                "补充: 企业消息链路中可使用 'publish_artifacts' 交付文件或图片。",
            )
        else:
            stable_policy_lines.insert(
                stable_policy_lines.index("策略 [元工具导航]:"),
                "普通桌面会话不要调用 'publish_artifacts'；若生成了本地文件或链接，请直接在最终回复里说明路径或地址。",
            )

        memory_lines = []
        memories_text = ""
        if self.config_manager:
            try:
                history_dir = self.config_manager.get_chat_history_dir()
                memories_path = os.path.join(history_dir, "memories.md")
                if os.path.exists(memories_path):
                    with open(memories_path, "r", encoding="utf-8") as f:
                        memories_text = f.read().strip()
            except Exception:
                memories_text = ""
        if memories_text:
            memory_lines.append("\n# Memories\n" + memories_text)

        return "\n".join(stable_policy_lines + memory_lines)

    def _build_runtime_context_prompt(self, current_messages, runtime_snapshot, sandbox_snapshot):
        python_exe = runtime_snapshot.get("python_exe") or get_python_executable()
        python_info = sandbox_snapshot.get("python") or {}
        node_info = sandbox_snapshot.get("node") or {}
        bash_info = sandbox_snapshot.get("bash") or {}
        available_runtimes = [
            name for name, info in (("Python", python_info), ("Node.js", node_info), ("Bash", bash_info))
            if info.get("available")
        ]
        missing_runtimes = [
            name for name, info in (("Python", python_info), ("Node.js", node_info), ("Bash", bash_info))
            if not info.get("available")
        ]
        sandbox_env_line = (
            f"沙盒运行时: 已内置/检测到 {', '.join(available_runtimes)}，可直接调用，无需要求用户安装。"
            if available_runtimes
            else "沙盒运行时: 未检测到可用 Python/Node.js/Bash。"
        )
        if missing_runtimes:
            sandbox_env_line += f" 缺失: {', '.join(missing_runtimes)}。"
        available_packages = runtime_snapshot.get("available_packages", [])
        missing_packages = runtime_snapshot.get("missing_packages", [])
        package_line = (
            f"运行时库检测(可用): {', '.join(available_packages[:10])}"
            if available_packages
            else "运行时库检测(可用): 未检测到"
        )
        missing_line = (
            f"运行时库检测(缺失): {', '.join(missing_packages[:10])}"
            if missing_packages
            else "运行时库检测(缺失): 无"
        )
        run_mode = self._current_run_mode()
        available_tool_names = self._available_tool_names()
        clarifying_read_tools = get_clarifying_read_tools(available_tool_names)

        capability_lines = []
        if available_tool_names:
            tool_lines = []
            chunk_size = 12
            for start in range(0, len(available_tool_names), chunk_size):
                chunk = available_tool_names[start : start + chunk_size]
                tool_lines.append("- " + ", ".join(f"`{name}`" for name in chunk))
            capability_lines.extend(
                [
                    "",
                    "当前可用工具清单（仅以下工具真正暴露给你，可直接调用）:",
                    *tool_lines,
                    "更多工具可能被延迟加载；需要额外能力时，先调用 `tool_search` 按关键词发现工具。",
                ]
            )
        else:
            capability_lines.extend(
                [
                    "",
                    "当前可用工具清单: 本轮未暴露任何工具。",
                ]
            )

        selected_skill_names = [
            skill_name for skill_name in self._selected_skill_names()
            if self.skill_manager.get_brief_skill_prompt(skill_name)
        ]
        dynamic_state_lines = [
            "",
            "# 当前运行状态",
            f"当前工作区: {self.workspace_dir}",
            f"当前运行模式: {run_mode}",
            f"当前日期: {getattr(self, '_prompt_context_date', '') or datetime.now().strftime('%Y-%m-%d')}",
            f"操作系统: {platform.system()} {platform.release()}",
            f"Python 版本: {sys.version.split()[0]}",
            f"运行时 Python 版本: {runtime_snapshot.get('version') or '未知'}",
            sandbox_env_line,
            f"Python 路径: {python_exe or '沙盒 Python 路径解析失败'}",
            f"Node.js 版本: {node_info.get('version') or '未知'}",
            f"Node.js 路径: {node_info.get('path') or '沙盒 Node.js 路径解析失败'}",
            f"Bash 版本: {bash_info.get('version') or '未知'}",
            f"Bash 路径: {bash_info.get('path') or '沙盒 Bash 路径解析失败'}",
            package_line,
            missing_line,
        ]
        if self._is_clarifying_mode():
            if clarifying_read_tools:
                read_tool_line = "、".join(f"`{name}`" for name in clarifying_read_tools)
            else:
                read_tool_line = "当前 tool schema 中未暴露工作区读取工具"
            dynamic_state_lines.extend(
                [
                    "",
                    "策略 [反问模式]:",
                    "1. 你当前处于 clarifying 模式。你必须先做只读探索，禁止执行会修改工作区或系统状态的操作。",
                    f"2. 当前反问模式下可用只读工具: {read_tool_line}。",
                    "3. 先探索再提问：优先通过代码和配置消除不确定性，不要提可以从仓库直接查到的问题。",
                    "4. 若需求仍不清楚，必须立即通过 'request_user_input' 以问卷卡片提出澄清问题；不要在普通文本回复中询问用户是否愿意进入反问。",
                    "5. 问题数量应弹性控制在 3-4 个；需求已经足够清楚时可以少问或不问。",
                    "6. 每个问题都要 materially 改变执行方案、确认重要假设，或选择真实取舍；选项要互斥且带简短说明。",
                    "7. 允许多轮反问；如果本轮回答后仍缺少关键决策，继续调用 'request_user_input'。",
                    "8. 当信息足够执行时，输出一段简短的已确认需求总结，不要输出计划文档或 XML 标签；UI 会切回正常执行模式继续同一任务。",
                    "9. 反问模式不会放宽任何权限边界：工作区外访问、写操作、命令执行、系统自动化等限制仍然有效。",
                ]
            )
        if self.parent_agent_id:
            dynamic_state_lines.append(f"Note: You are a sub-agent (ID: {self.parent_agent_id}). Perform your assigned task efficiently.")

        agent_profile_name = str(self.run_context.get("agent_profile_name") or "").strip()
        agent_description = str(self.run_context.get("agent_description") or "").strip()
        agent_system_prompt = str(self.run_context.get("agent_system_prompt") or "").strip()
        if agent_profile_name or agent_system_prompt:
            agent_lines = ["# 智能体角色"]
            if agent_profile_name:
                agent_lines.append(f"当前智能体: {agent_profile_name}")
            if agent_description:
                agent_lines.append(agent_description)
            if agent_system_prompt:
                agent_lines.append(agent_system_prompt)
            dynamic_state_lines.append("\n" + "\n".join(agent_lines))

        if selected_skill_names:
            selected_skill_lines = [
                f"- `{skill_name}`: {self.skill_manager.get_skill_display_name(skill_name)}"
                for skill_name in selected_skill_names
            ]
            selected_skill_briefs = [
                self.skill_manager.get_brief_skill_prompt(skill_name)
                for skill_name in selected_skill_names
            ]
            dynamic_state_lines.extend(
                [
                    "",
                    "# 用户指定能力",
                    "以下能力由用户通过对话栏的插件入口为当前会话明确指定。除非明显不适用，或受当前运行模式/权限限制，你必须优先参考并使用这些能力。",
                    *selected_skill_lines,
                    "",
                    "这些用户指定能力的简版说明如下：",
                    "\n\n".join([item for item in selected_skill_briefs if item]),
                ]
            )

        sop_prompt_fragment = build_sop_prompt_fragment(self.run_context.get("sop_run"))
        if sop_prompt_fragment:
            dynamic_state_lines.extend(["", sop_prompt_fragment])

        context_lines = capability_lines + dynamic_state_lines
        return "\n".join(context_lines)

    def _build_system_prompt(self, current_messages, runtime_snapshot, sandbox_snapshot):
        stable_prompt = self._build_stable_system_prompt()
        runtime_prompt = self._build_runtime_context_prompt(current_messages, runtime_snapshot, sandbox_snapshot)
        return "\n".join([part for part in (stable_prompt, runtime_prompt) if part])

    def _get_stable_system_prompt(self):
        if self._stable_system_prompt is None:
            self._stable_system_prompt = self._build_stable_system_prompt()
        return self._stable_system_prompt

    def _build_request_messages(self, current_messages, runtime_context_prompt):
        request_messages = [msg.copy() if isinstance(msg, dict) else msg for msg in current_messages]
        if runtime_context_prompt:
            request_messages.append({"role": "system", "content": runtime_context_prompt})
        return request_messages

    def _emit_prompt_observability(self, stable_prompt, runtime_prompt, request_messages):
        skill_contexts = []
        for msg in request_messages or []:
            if not isinstance(msg, dict):
                continue
            meta = msg.get("meta") if isinstance(msg.get("meta"), dict) else {}
            if meta.get("kind") not in ("skill_context", "skill_context_update"):
                continue
            skill_contexts.append({
                "type": "system_prompt_append",
                "kind": meta.get("kind"),
                "source": meta.get("source") or "skill_context",
                "skill_names": [meta.get("skill_name")] if meta.get("skill_name") else [],
                "content": msg.get("content") or "",
            })
        self.observability_signal.emit({
            "type": "system_prompt",
            "content": stable_prompt,
            "runtime_context": runtime_prompt,
            "skill_contexts": skill_contexts,
            "prompt_cache_key": self.conversation_id or self.session_id,
            "timestamp": time.time(),
            "run_mode": self._current_run_mode(),
        })

    def _provider_chat_stream(self, provider, messages, tools, prompt_cache_key):
        try:
            return provider.chat_stream(
                messages,
                tools=tools,
                prompt_cache_key=prompt_cache_key,
            )
        except TypeError:
            return provider.chat_stream(messages, tools=tools)

    def run(self):
        # Work on a copy of messages to handle multi-turn locally
        # Keep reasoning_content only on prior assistant tool-call turns so DeepSeek
        # can continue the same multi-round exchange without 400 errors.
        current_messages = repair_tool_call_sequence(clear_reasoning_content(self.messages))
        runtime_snapshot = get_python_runtime_snapshot()
        sandbox_snapshot = get_runtime_snapshot()
        stable_system_prompt = self._get_stable_system_prompt()
        current_messages.insert(0, {"role": "system", "content": stable_system_prompt})
        
        full_reasoning = ""
        final_content = ""
        turn_count = 0
        total_duration = 0
        generated_messages = []
        
        last_tool_signature = None
        repetition_count = 0
        
        last_turn_reasoning = None
        reasoning_repetition_count = 0
        force_reply_attempted = False
        
        disclosed_skills = set()
        while True:
            # Check Control Flags
            while self.is_paused:
                if self.is_stopped: break
                self.msleep(100)
            if self.is_stopped: 
                final_content = "⚠️ Operation stopped by user."
                break

            self._append_pending_guidance(current_messages, generated_messages)

            turn_count += 1
            self._refresh_tool_definitions()
            self.step_signal.emit(f"Turn {turn_count}: Requesting LLM...")

            # --- Hot Reload Skills ---
            # Check if any new skills were added or modified
            if self.skill_manager.check_for_updates():
                self.step_signal.emit("System: Detecting skill updates... Reloading.")
                self.skill_manager.load_skills()
                self._refresh_tool_definitions()
                disclosed_skills.clear()
            # -------------------------

            stable_system_prompt = self._get_stable_system_prompt()
            current_messages[0]["content"] = stable_system_prompt
            runtime_context_prompt = self._build_runtime_context_prompt(
                current_messages[1:],
                runtime_snapshot,
                sandbox_snapshot,
            )
            request_messages = self._build_request_messages(current_messages, runtime_context_prompt)
            self._emit_prompt_observability(stable_system_prompt, runtime_context_prompt, request_messages)

            # Reset reasoning for the current turn (for UI display)
            current_turn_reasoning = ""

            if self.api_key:
                try:
                    start_time = time.time()
                    
                    # Create Provider via Factory
                    provider = LLMFactory.create_provider(
                        self.config_manager,
                        self.run_context.get("selected_model_id"),
                    )
                    provider_name = getattr(provider, "provider_name", None) or provider.__class__.__name__
                    self.step_signal.emit(f"Provider Start: {provider_name}")
                    require_reasoning_replay = bool(
                        is_deepseek_request(
                            getattr(provider, "model_name", ""),
                            getattr(provider, "base_url", ""),
                        ) and getattr(provider, "thinking_enabled", False)
                    )
                    sanitized_messages, sanitize_meta = sanitize_llm_messages(
                        request_messages,
                        require_reasoning_replay=require_reasoning_replay,
                        return_metadata=True,
                    )
                    dropped_rounds = sanitize_meta.get("dropped_incomplete_reasoning_rounds") or []
                    if dropped_rounds:
                        dropped_calls = sum(len(item.get("tool_call_ids") or []) for item in dropped_rounds)
                        self.step_signal.emit(
                            "System: Pruned "
                            f"{len(dropped_rounds)} DeepSeek tool-call history round(s) "
                            f"missing reasoning_content before replay ({dropped_calls} tool call(s))."
                        )
                    # Streaming Buffers
                    chunk_reasoning = ""
                    chunk_content = ""
                    tool_calls_buffer = {} # Index -> ToolCall object (dict)
                    provider_error_message = None
                    protocol_locked = False

                    try:
                        if _needs_openai_protocol_lock(provider):
                            waited_for_protocol = _acquire_openai_protocol_lock(self)
                            if waited_for_protocol is None:
                                final_content = "⚠️ Operation stopped by user."
                                break
                            protocol_locked = True
                            if waited_for_protocol:
                                self.step_signal.emit("Provider Protocol: waited for OpenAI-compatible stream lock.")
                        stream = self._provider_chat_stream(
                            provider,
                            sanitized_messages,
                            tools=self._tools_for_messages(sanitized_messages),
                            prompt_cache_key=self.conversation_id or self.session_id,
                        )

                        for chunk in stream:
                            # Check Pause/Stop during stream
                            while self.is_paused:
                                 if self.is_stopped: break
                                 self.msleep(100)
                            if self.is_stopped: break

                            type_ = chunk.get("type")

                            # 1. Handle Reasoning
                            if type_ == "reasoning":
                                r_content = chunk["content"]
                                current_turn_reasoning += r_content
                                full_reasoning += r_content
                                self.thinking_signal.emit(r_content)

                            # 2. Handle Content
                            elif type_ == "content":
                                c_content = chunk["content"]
                                chunk_content += c_content
                                self.content_signal.emit(c_content)

                            # 3. Handle Tool Calls
                            elif type_ == "tool_call":
                                index = chunk.get("index", 0) # Default to 0 if not provided
                                if index is None:
                                    index = 0
                                chunk_function = chunk.get("function") or {}
                                if not isinstance(chunk_function, dict):
                                    chunk_function = {}

                                if index not in tool_calls_buffer:
                                    tool_calls_buffer[index] = {
                                        "id": chunk.get("id") or "",
                                        "type": "function",
                                        "function": {
                                            "name": "",
                                            "arguments": ""
                                        }
                                    }

                                chunk_id = chunk.get("id")
                                if chunk_id:
                                    tool_calls_buffer[index]["id"] = chunk_id

                                chunk_name = chunk_function.get("name")
                                if chunk_name:
                                    tool_calls_buffer[index]["function"]["name"] = str(chunk_name)

                                # Append arguments
                                if "arguments" in chunk_function:
                                    raw_arguments = chunk_function.get("arguments")
                                    if raw_arguments is None:
                                        arguments_part = ""
                                    elif isinstance(raw_arguments, str):
                                        arguments_part = raw_arguments
                                    else:
                                        try:
                                            arguments_part = json.dumps(raw_arguments, ensure_ascii=False)
                                        except Exception:
                                            arguments_part = str(raw_arguments)
                                    tool_calls_buffer[index]["function"]["arguments"] += arguments_part

                            # 4. Handle Error
                            elif type_ == "error":
                                provider_error_message = chunk.get("content") or "Unknown error"
                                self.step_signal.emit(f"Provider Error: {provider_error_message}")
                                self.output_signal.emit(f"Provider Error: {provider_error_message}")
                            elif type_ == "usage":
                                usage_payload = dict(chunk.get("usage") or {})
                                usage_payload.setdefault("prompt_cache_key", self.conversation_id or self.session_id)
                                self.observability_signal.emit({
                                    "type": "llm_usage",
                                    "usage": usage_payload,
                                    "timestamp": time.time(),
                                })
                    finally:
                        if protocol_locked:
                            _OPENAI_PROTOCOL_LOCK.release()

                    end_time = time.time()
                    duration = end_time - start_time
                    total_duration += duration
                    self.step_signal.emit(f"Provider End: {provider_name} ({duration:.2f}s)")
                    
                    # --- Reasoning Loop Detection ---
                    if current_turn_reasoning and len(current_turn_reasoning) > 10: # Ignore very short reasonings
                        if current_turn_reasoning == last_turn_reasoning:
                            reasoning_repetition_count += 1
                        else:
                            reasoning_repetition_count = 0
                            last_turn_reasoning = current_turn_reasoning
                            
                        if reasoning_repetition_count >= 3:
                            self.step_signal.emit("系统: 🛑 检测到思维死循环 (重复的思考过程)。自动停止。")
                            final_content = "⚠️ 操作已停止: 检测到思维死循环 (重复的思考过程)。"
                            break
                    # --------------------------------

                    # Reconstruct final message object from buffers
                    content = chunk_content
                    if provider_error_message and not content and not tool_calls_buffer:
                        content = f"⚠️ Provider Error: {provider_error_message}"
                    
                    # Reconstruct tool_calls list
                    tool_calls = []
                    if tool_calls_buffer:
                        # Convert buffer to list of objects mimicking OpenAI ToolCall
                        # We need to be careful to match the structure expected by the loop logic
                        for idx in sorted(tool_calls_buffer.keys()):
                            t_data = tool_calls_buffer[idx]
                            # Create a simple object structure
                            class ToolCallObj:
                                pass
                            class FunctionObj:
                                pass
                                
                            t_obj = ToolCallObj()
                            t_obj.id = t_data["id"]
                            t_obj.type = t_data["type"]
                            t_obj.function = FunctionObj()
                            t_obj.function.name = t_data["function"]["name"]
                            t_obj.function.arguments = t_data["function"]["arguments"]
                            
                            tool_calls.append(t_obj)

                    invalid_tool_calls = [
                        tool for tool in tool_calls
                        if not str(getattr(tool.function, "name", "") or "").strip()
                    ]
                    if invalid_tool_calls:
                        malformed_prompt = (
                            "上一轮 provider 返回了缺少 function.name 的无效 tool_call。"
                            "忽略任何原始参数片段或内部工具发现说明；"
                            "如果需要工具，请仅发出带完整函数名的有效 tool_call，否则直接回答用户。"
                        )
                        self.step_signal.emit(
                            "System: Provider emitted malformed tool calls without function.name; ignoring them."
                        )
                        self.output_signal.emit(
                            "Tool Call Error: Provider returned malformed tool calls without function.name."
                        )
                        current_messages.append({
                            "role": "system",
                            "content": malformed_prompt,
                        })
                        self.observability_signal.emit({
                            "type": "system_prompt_append",
                            "content": malformed_prompt,
                            "source": "invalid_tool_call_recovery",
                            "timestamp": time.time(),
                        })
                    tool_calls = [
                        tool for tool in tool_calls
                        if str(getattr(tool.function, "name", "") or "").strip()
                    ]

                    if tool_calls:
                        self._append_skill_prompts(tool_calls, current_messages, disclosed_skills, generated_messages)

                    if (not tool_calls) and (not (content or "").strip()) and (not provider_error_message):
                        if not force_reply_attempted:
                            force_reply_attempted = True
                            self.step_signal.emit("System: Empty content detected, requesting a forced final answer.")
                            force_prompt = "你必须立即给出给用户可见的最终答复。禁止只输出思考内容。除非绝对必要，不要继续调用工具。请基于已有上下文与工具结果，直接输出清晰结论。"
                            current_messages.append({
                                "role": "system",
                                "content": force_prompt
                            })
                            self.observability_signal.emit({
                                "type": "system_prompt_append",
                                "content": force_prompt,
                                "source": "force_final_answer",
                                "timestamp": time.time(),
                            })
                            continue
                        content = "任务已完成，请查看上方工具执行结果。"

                    # Append Assistant Message to History (Manual reconstruction)
                    assistant_msg = {
                        "id": uuid.uuid4().hex,
                        "role": "assistant",
                        "content": content
                    }
                    # CRITICAL: For tool calls WITHIN the same turn, DeepSeek requires reasoning_content
                    # We must use current_turn_reasoning, NOT full_reasoning, to avoid duplication in history
                    # Always include the key, even if empty, to satisfy API requirements
                    assistant_msg["reasoning_content"] = current_turn_reasoning
                    # Also add 'reasoning' for UI compatibility (used by MainWindow)
                    assistant_msg["reasoning"] = current_turn_reasoning
                        
                    if tool_calls:
                         # For history, we need the dict representation
                         assistant_msg["tool_calls"] = [
                             {
                                 "id": t.id,
                                 "type": t.type,
                                 "function": {
                                     "name": t.function.name,
                                     "arguments": t.function.arguments
                                 }
                             } for t in tool_calls
                         ]
                    current_messages.append(assistant_msg)
                    generated_messages.append(assistant_msg)
                    
                    if tool_calls:
                        # --- Loop Detection ---
                        try:
                            current_signature = json.dumps(
                                sorted([{"name": t.function.name, "args": json.loads(t.function.arguments)} for t in tool_calls], key=lambda x: x['name']),
                                sort_keys=True
                            )
                            if current_signature == last_tool_signature:
                                repetition_count += 1
                            else:
                                repetition_count = 0
                                last_tool_signature = current_signature
                                
                            if repetition_count >= 3: # Same toolset called 4 times in a row
                                self.step_signal.emit("系统: 🛑 检测到循环 (重复的工具调用)。自动停止。")
                                final_content = "⚠️ 操作已停止: 检测到死循环 (重复的工具调用)。"
                                break
                        except Exception as e:
                            print(f"Loop detection error: {e}")
                        # ----------------------

                        self.step_signal.emit(f"Tool Calls Detected: {len(tool_calls)}")
                        for tool in tool_calls:
                            # Check Control Flags inside tool loop
                            while self.is_paused:
                                if self.is_stopped: break
                                self.msleep(100)
                            if self.is_stopped: break
                            
                            name = str(tool.function.name or "").strip()
                            raw_args = tool.function.arguments
                            args = {}
                            if isinstance(raw_args, dict):
                                args = raw_args
                            elif isinstance(raw_args, str) and raw_args.strip():
                                try:
                                    args = json.loads(raw_args)
                                except Exception:
                                    args = {}
                                    self.output_signal.emit(f"Tool Args Parse Fallback: {name} received invalid JSON arguments.")
                            missing_tool_name = not name
                            missing_tool_name_message = (
                                "Provider returned a tool call without function.name; "
                                "tool execution was skipped."
                            )
                            if missing_tool_name:
                                self.step_signal.emit(f"Tool Call Error: {missing_tool_name_message}")
                                self.output_signal.emit(f"Tool Call Error: {missing_tool_name_message}")
                            else:
                                self.step_signal.emit(f"Executing Tool: {name}({args})")

                                # Emit Tool Call Signal
                                self.tool_call_signal.emit({
                                    "id": tool.id,
                                    "name": name,
                                    "args": args
                                })
                                self.observability_signal.emit({
                                    "type": "tool_call",
                                    "id": tool.id,
                                    "name": name,
                                    "args": args,
                                    "timestamp": time.time(),
                                })

                                # Report Active Skill
                                skill_name = self.skill_manager.get_skill_of_tool(name)
                                if skill_name:
                                    self.skill_used_signal.emit(skill_name)
                            
                            # Execute via Skill Manager
                            # Pass step_signal as context to allow tools to log
                            start_tool_time = time.time()
                            current_snapshot = []
                            for msg in current_messages:
                                if not isinstance(msg, dict):
                                    continue
                                if msg.get("role") == "system":
                                    continue
                                current_snapshot.append(msg.copy())
                            tool_context = {
                                "session_id": self.session_id,
                                "conversation_id": self.conversation_id,
                                "workspace_dir": self.workspace_dir,
                                "automation_runner": self.automation_runner,
                                "step_signal": self.step_signal,
                                "config_manager": self.config_manager,
                                "skill_manager": self.skill_manager,
                                "chat_storage": self.chat_storage,
                                "agent_manager": self.agent_manager,
                                "file_state": self.file_state_cache,
                                "agent_state_signal": self.agent_state_signal,
                                "tool_call_id": tool.id,
                                "abort_signal": self.abort_signal,
                                "current_agent_id": self.agent_id or (self.parent_agent_id or ""),
                                "parent_agent_id": self.parent_agent_id or "",
                                "is_subagent": self.is_subagent,
                                "current_messages_snapshot": current_snapshot,
                                "run_context": json_copy(self.run_context, {}),
                                "discovered_tool_names": self.discovered_tool_names,
                            }
                            if missing_tool_name:
                                result = {
                                    "error": missing_tool_name_message,
                                    "blocked_tool": name,
                                    "status": "invalid_tool_call",
                                    "content": "模型返回了缺少函数名的工具调用，已跳过执行。",
                                }
                            elif self.is_subagent and name in AGENT_MANAGEMENT_TOOLS:
                                result = {
                                    "error": "sub-agents cannot manage other agents",
                                    "blocked_tool": name,
                                    "status": "denied",
                                }
                            elif not self._is_tool_allowed_for_mode(name):
                                result = {
                                    "error": f"Tool '{name}' is not allowed in {self._current_run_mode()} mode.",
                                    "blocked_tool": name,
                                    "status": "denied",
                                    "mode": self._current_run_mode(),
                                    "content": f"当前模式禁止执行 {name}，请改用允许的工具或切换模式。",
                                }
                            elif not self._is_tool_visible_for_run(name):
                                result = {
                                    "error": f"Tool '{name}' has not been discovered for this run.",
                                    "blocked_tool": name,
                                    "status": "denied",
                                    "mode": self._current_run_mode(),
                                    "content": f"请先调用 tool_search 发现 {name}，再在下一轮使用它。",
                                }
                            else:
                                result = self.skill_manager.call_tool(
                                    name,
                                    args,
                                    context=tool_context,
                                )
                                if name == "tool_search":
                                    self._refresh_tool_definitions()
                            end_tool_time = time.time()
                            duration_tool = end_tool_time - start_tool_time

                            result_obj = result if isinstance(result, dict) else None
                            if isinstance(result, dict):
                                try:
                                    result_text = json.dumps(result, ensure_ascii=False)
                                except Exception:
                                    result_text = str(result)
                            else:
                                result_text = str(result)
                            
                            # Emit Tool Result Signal
                            self.tool_result_signal.emit({
                                "id": tool.id,
                                "name": name,
                                "args": args,
                                "result": result_text,
                                "result_obj": result_obj,
                                "meta": {
                                    "start_time": start_tool_time,
                                    "end_time": end_tool_time,
                                    "duration": duration_tool
                                }
                            })
                            self.observability_signal.emit({
                                "type": "tool_result",
                                "id": tool.id,
                                "name": name,
                                "args": args,
                                "result": result_text,
                                "result_obj": result_obj,
                                "timestamp": end_tool_time,
                                "meta": {
                                    "start_time": start_tool_time,
                                    "end_time": end_tool_time,
                                    "duration": duration_tool
                                }
                            })

                            tool_msg = {
                                "id": uuid.uuid4().hex,
                                "role": "tool",
                                "tool_call_id": tool.id,
                                "content": result_text,
                                "result_obj": result_obj,
                                "meta": {
                                    "start_time": start_tool_time,
                                    "end_time": end_tool_time,
                                    "duration": duration_tool
                                }
                            }
                            current_messages.append(tool_msg)
                            if name == "tool_search":
                                self._append_tool_search_skill_prompts(result_obj, current_messages, disclosed_skills, generated_messages)
                            generated_messages.append(tool_msg)
                            self.step_signal.emit(f"Tool Result: {result_text}")
                        # Loop continues to let LLM see tool results
                        continue
                    else:
                        # Final Answer
                        if self._append_pending_guidance(
                            current_messages,
                            generated_messages,
                            close_if_empty=True,
                        ):
                            force_reply_attempted = False
                            continue
                        final_content = content
                        break
                        
                except Exception as e:
                    self._append_pending_guidance(current_messages, generated_messages, close=True)
                    self.output_signal.emit(f"Provider Exception: {e}")
                    self.finished_signal.emit({"error": str(e), "generated_messages": generated_messages})
                    return
            else:
                # --- Mock Logic / Warning for Missing API Key ---
                time.sleep(1)
                
                reasoning = "检测到 API Key 未配置或 OpenAI 库不可用。无法连接到 DeepSeek 模型。"
                full_reasoning += f"\n[System]: {reasoning}"
                self.step_signal.emit(f"System: {reasoning}")
                
                final_content = (
                    "⚠️ **未配置 API Key**\n\n"
                    "请点击左侧边栏的 **⚙️ 系统设置**，在其中配置您的 DeepSeek API Key。\n"
                    "配置完成后，我将能够为您执行复杂的文件操作和代码生成任务。"
                )
                
                break

        self._append_pending_guidance(current_messages, generated_messages, close=True)
        self.finished_signal.emit({
            "reasoning": full_reasoning.strip(),
            "content": final_content,
            "role": "assistant",
            "duration": total_duration,
            "generated_messages": generated_messages
        })

        self.agent_state_signal.emit({
            "agent_id": self.agent_id or self.parent_agent_id or "Main",
            "status": "completed", 
            "content": final_content
        })
