---
name: python-runner
description: Execute Python code to perform tasks that are not covered by other tools.
description_cn: 执行 Python 代码以完成其他工具未涵盖的任务。
license: Apache-2.0
metadata:
  author: deepseek-cowork team
  version: "1.0"
allowed-tools: run_python_code, install_package
---

# Python Runner Skill

This skill allows the agent to execute arbitrary Python code within the workspace and manage Python packages.
Use this when you need to calculate data, process text, or perform tasks where no specific tool exists.

## Capabilities
1. **Run Python Code**: Execute a Python script and get the stdout/stderr.
2. **Install Package**: Install a Python package from PyPI and ensure it's available for immediate use (Hot Reload).

## Usage Guidelines
- **Sandboxed**: Code runs in the user's workspace.
- **Security**: File operations are restricted to the workspace.
- **Dependencies**: Only the standard library and packages verified in the active sandbox or Skill dependency environment may be assumed available. Validate the actual import or dependency result for the task.
- **Availability**: This is a core built-in Skill. Its tools appear directly in the execution-mode tool list without a prior `tool_search`.
- **Tool Choice**: Prefer `run_python_code` for data processing, calculations, structured text transforms, and lightweight file analysis; use `bash` only when the real shell or an existing CLI is needed.
- **Parallelism**: Python execution and package installation are not read-only batch operations and must not be called through `parallel_tools`.

## God Mode (System Operations)
When God Mode is enabled:
- **Full Access**: Path traversal checks are disabled (access C:/, D:/, etc.).
- **Dangerous Modules**: `subprocess`, `winreg`, `ctypes` are allowed.
- **Use Cases**: Registry editing, Process management (kill/list), System service management.
- **Caution**: Always explain the intent clearly to the user before running dangerous code.

## Commands

### `install_package`

Installs a Python package using pip and hot-reloads the environment so it can be imported immediately.

**Parameters:**
- `package_name` (string): The name of the package to install (e.g., `pandas`, `requests`).
- `import_name` (string, optional): The module name if different from package name (e.g., package `beautifulsoup4` -> import `bs4`).

**Example:**
```json
{
  "name": "install_package",
  "arguments": {
    "package_name": "pandas"
  }
}
```

## Current Runtime Notes
- The app injects one sandbox runtime snapshot into the system context, including Python, Node.js, and Bash availability, versions, and paths. It does not inject a static package inventory.
- Package availability is task- and Skill-specific; use the actual Tool result or declared dependency preparation instead of inferring it from a global list.
- Do not ask the user to install Python/Node/Bash merely because PATH looks empty; verify runtime availability through the sandbox context or direct tool output first.
- When a task is ambiguous, use read-oriented tools first and avoid executing arbitrary Python until the execution target is clear.
