---
name: python-runner
description: Execute Python code to perform tasks that are not covered by other tools.
description_cn: 执行 Python 代码以完成其他工具未涵盖的任务。
license: Apache-2.0
metadata:
  author: cowork-team
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
- **Dependencies**: Standard library + installed packages (pandas, openpyxl, etc.) are available.

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
