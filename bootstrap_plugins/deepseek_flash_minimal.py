import base64
import os
import shutil
import subprocess
import time
from urllib.parse import urlsplit

from core.process_utils import terminate_process_tree
from core.sandbox_runtime import run_in_sandbox


class DeepSeekFlashMinimalBootstrap:
    plugin_id = "deepseek_flash_minimal"

    def __init__(self):
        self.executable = ""

    def supports(self, provider, model):
        # Exact current model and official HTTPS endpoint; no name/host substring matches.
        if str(model or "").strip() != "deepseek-flash":
            return False
        if str(provider.get("provider_type") or provider.get("provider") or "openai").lower() != "openai":
            return False
        try:
            url = urlsplit(str(provider.get("base_url") or "").strip())
            return bool(
                url.scheme == "https"
                and url.hostname == "api.deepseek.com"
                and url.port in (None, 443)
                and url.path.rstrip("/") in ("", "/v1")
                and not (url.username or url.password or url.query or url.fragment)
            )
        except ValueError:
            return False

    def system_prompt(self):
        return "You are a helpful software engineer assistant."

    def tools(self):
        return ["pwsh"]

    def tool_definitions(self):
        return [{
            "type": "function",
            "function": {
                "name": "pwsh",
                "description": (
                    "Run a short command in a fresh PowerShell process in the task workspace. "
                    "Use native Windows paths and $env:NAME. Avoid large output. "
                    "State does not persist between calls."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                    "additionalProperties": False,
                },
            },
        }]

    def prepare(self):
        # Both PowerShell editions accept the same encoded command and tool schema.
        self.executable = shutil.which("pwsh") or shutil.which("powershell") or ""
        if not self.executable and os.name == "nt":
            system_root = os.environ.get("SystemRoot", "")
            legacy = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
            if system_root and os.path.isfile(legacy):
                self.executable = legacy
        if not self.executable:
            raise FileNotFoundError("PowerShell is unavailable (checked pwsh and Windows PowerShell).")

    def call_tool(self, name, args, context):
        if name != "pwsh":
            raise ValueError(f"Unknown bootstrap tool: {name}")
        if not isinstance(args, dict) or set(args) != {"command"}:
            return {"status": "error", "error": "pwsh requires only a command string."}
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return {"status": "error", "error": "pwsh requires a non-empty command."}
        workspace = context.get("workspace_dir")
        if not workspace or not os.path.isdir(workspace):
            return {"status": "error", "error": "The task workspace is unavailable."}
        aborted = context.get("abort_check", lambda: False)
        if aborted():
            return {"status": "denied", "error": "Stopped before command execution."}

        # Keep the submitted command out of cmd.exe/bash quoting and preserve Unicode.
        script = (
            "$ErrorActionPreference = 'Stop'\n"
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
            "$OutputEncoding = [Console]::OutputEncoding\n"
            "try {\n& {\n" + command + "\n}\n"
            "if (-not $?) { exit 1 }\n"
            "if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }\n"
            "} catch { [Console]::Error.WriteLine($_.ToString()); exit 1 }\n"
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        process = None
        try:
            process = run_in_sandbox(
                [self.executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                cwd=workspace,
                shell_kind="exec",
                text=True,
            )
            deadline = time.monotonic() + 60
            while True:
                if aborted() or time.monotonic() >= deadline:
                    reason = "Command stopped by user." if aborted() else "Bootstrap command timed out after 60 seconds."
                    terminate_process_tree(process)
                    stdout, stderr = process.communicate(timeout=2)
                    return {
                        "status": "unknown",
                        "error": reason,
                        "content": ((stdout or "") + (stderr or ""))[:16000],
                    }
                try:
                    stdout, stderr = process.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    continue
            content = (stdout or "") + ("\nSTDERR:\n" + stderr if stderr else "")
            result = {
                "status": "success" if process.returncode == 0 else "error",
                "exit_code": process.returncode,
                "content": content[:16000] or "(No output)",
            }
            if len(content) > 16000:
                result["content"] += "\n[Output truncated at 16000 characters]"
            if process.returncode != 0:
                result["error"] = f"PowerShell exited with code {process.returncode}."
            return result
        except Exception as exc:
            return {
                "status": "unknown" if process is not None else "error",
                "error": str(exc),
            }
        finally:
            if process is not None and process.poll() is None:
                terminate_process_tree(process)
