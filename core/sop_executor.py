import locale
import os
import subprocess
import time

from core.sandbox_runtime import get_runtime_executable, run_in_sandbox
from core.sop_manager import (
    SOP_EXECUTOR_BASH_COMMAND,
    SOP_EXECUTOR_PYTHON_FILE,
    get_current_step,
    normalize_sop_run,
)


def _decode_bytes(raw):
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    candidates = ["utf-8", "utf-8-sig"]
    preferred = locale.getpreferredencoding(False)
    if preferred:
        candidates.append(preferred)
    if os.name == "nt":
        candidates.extend(["mbcs", "cp936", "gbk", "cp950", "cp932"])
    best = None
    best_score = -1
    for enc in candidates:
        try:
            text = raw.decode(enc, errors="replace")
        except Exception:
            continue
        score = -text.count("�")
        if any("\u4e00" <= ch <= "\u9fff" for ch in text):
            score += 5
        if score > best_score:
            best = text
            best_score = score
    if best is not None:
        return best
    return raw.decode("utf-8", errors="replace")


def _output_summary(stdout="", stderr="", error=""):
    parts = []
    if stdout:
        parts.extend(["STDOUT:", stdout.strip()])
    if stderr:
        parts.extend(["STDERR:", stderr.strip()])
    if error:
        parts.extend(["ERROR:", error.strip()])
    return "\n".join([part for part in parts if part]).strip() or "(No output)"


def execute_sop_step(run, workspace_dir):
    normalized = normalize_sop_run(run)
    step = get_current_step(normalized)
    if not normalized or not step:
        return {"ok": False, "error": "No active SOP step.", "content": "No active SOP step."}

    executor_type = str(step.get("executor_type") or "").strip()
    timeout_seconds = int(step.get("timeout_seconds") or 300)
    start = time.time()
    payload = {
        "ok": False,
        "executor_type": executor_type,
        "executor": executor_type,
        "started_at": int(start),
        "finished_at": 0,
        "duration_seconds": 0,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "error": "",
        "content": "",
    }

    try:
        if executor_type == SOP_EXECUTOR_PYTHON_FILE:
            python_exe = get_runtime_executable("python")
            if not python_exe:
                raise FileNotFoundError("Bundled Python runtime is missing.")
            script = step.get("python_script") or {}
            script_path = os.path.abspath(str(script.get("path") or ""))
            if not script_path.lower().endswith(".py"):
                raise ValueError("Only .py files can be executed as SOP Python steps.")
            if not os.path.isfile(script_path):
                raise FileNotFoundError(f"Python script not found: {script_path}")
            command = [python_exe, "-X", "utf8", script_path]
            process = run_in_sandbox(
                command,
                cwd=workspace_dir or os.getcwd(),
                skill_id="sop-executor",
                shell_kind="exec",
                text=False,
            )
        elif executor_type == SOP_EXECUTOR_BASH_COMMAND:
            bash_exe = get_runtime_executable("bash")
            if not bash_exe:
                raise FileNotFoundError("Sandbox Bash runtime is missing.")
            command = str(step.get("bash_command") or "").strip()
            if not command:
                raise ValueError("Bash command is empty.")
            process = run_in_sandbox(
                [bash_exe, "-lc", command],
                cwd=workspace_dir or os.getcwd(),
                skill_id="sop-executor",
                shell_kind="exec",
                text=False,
            )
        else:
            raise ValueError(f"Unsupported SOP executor type: {executor_type}")

        stdout_raw, stderr_raw = process.communicate(timeout=timeout_seconds)
        payload["exit_code"] = process.returncode
        payload["stdout"] = _decode_bytes(stdout_raw).strip()
        payload["stderr"] = _decode_bytes(stderr_raw).strip()
        payload["ok"] = process.returncode == 0
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except Exception:
            pass
        payload["error"] = f"Execution timed out after {timeout_seconds} seconds."
        payload["exit_code"] = -1
    except Exception as exc:
        payload["error"] = str(exc)
        payload["exit_code"] = -1
    finally:
        finished = time.time()
        payload["finished_at"] = int(finished)
        payload["duration_seconds"] = round(max(0, finished - start), 3)

    payload["content"] = _output_summary(payload["stdout"], payload["stderr"], payload["error"])
    return payload
