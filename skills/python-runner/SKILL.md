---
name: python-runner
description: Execute Python code to perform tasks that are not covered by other tools.
description_cn: 执行 Python 代码以完成其他工具未涵盖的任务。
license: Apache-2.0
metadata:
  author: cowork-team
  version: "1.0"
allowed-tools: run_python_code
---

# Python Runner Skill

This skill allows the agent to execute arbitrary Python code within the workspace.
Use this when you need to calculate data, process text, or perform tasks where no specific tool exists.

## Capabilities
1. **Run Python Code**: Execute a Python script and get the stdout/stderr.

## Usage Guidelines
- **Sandboxed**: Code runs in the user's workspace.
- **Security**: File operations are restricted to the workspace.
- **Dependencies**: Standard library + installed packages (pandas, openpyxl, etc.) are available.

## God Mode (System Operations)
When God Mode is enabled:
- **Full Access**: Path traversal checks are disabled (access C:/, D:/, etc.).
- **Dangerous Modules**: `subprocess`, `winreg`, `ctypes` are allowed.
- **Use Cases**: Registry editing, Process management (kill/list), System service management.
- **Caution**: Always explain the intent clearly to the user before running dangerous code.
