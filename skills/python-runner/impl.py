from core.tool_cancellation import init_abort_state as _init_abort_state
from core.tool_output import capture_process, process_text
import tempfile
import os
from core.env_utils import ensure_package_installed
from core.sandbox_runtime import get_runtime_executable, run_in_sandbox

def install_package(package_name, import_name=None, _context=None):
    """
    Install a Python package and hot-reload it.
    
    Args:
        package_name (str): The pip package name.
        import_name (str, optional): The import module name.
    """
    try:
        ensure_package_installed(package_name, import_name, skill_id="python-runner", context=_context, retry=True)
        return f"Successfully installed and verified '{package_name}' for run_python_code."
    except Exception as e:
        if getattr(e, 'result', {}).get('status') in {'cancelled', 'unknown'}:
            return {"ok": False, "status": e.result['status'], "error": str(e), "content": "代码尚未执行，已有安装日志已保留。"}
        return {"ok": False, "status": "failed", "error": f"安装 {package_name} 失败：{e}", "content": "代码尚未执行。可修正原因后重试安装。"}

def run_python_code(workspace_dir, code, _context=None):
    """
    Execute Python code in the workspace.
    
    Args:
        workspace_dir (str): Root workspace directory.
        code (str): Python code to execute.
    """
    if not workspace_dir:
        return "Error: Workspace not selected."
        
    visualization_dir = ""
    manager = (_context or {}).get("skill_manager") if isinstance(_context, dict) else None
    visualize_enabled = "visualize" in (getattr(manager, "skill_records", {}) or {})
    if visualize_enabled:
        from core.inline_visualization import visualization_staging_dir

        conversation_id = str((_context or {}).get("conversation_id") or (_context or {}).get("session_id") or "").strip()
        if conversation_id:
            visualization_dir = visualization_staging_dir(conversation_id, create=True)

    # Create temp file
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(code)
            temp_path = f.name
    except Exception as e:
        return f"Error creating temp file: {e}"

    # Determine python executable
    python_exe = get_runtime_executable("python")
    if not python_exe:
        return "Error: Bundled Python runtime is missing. This package may be corrupted. Please reinstall the application."
    
    try:
        abort_state = _init_abort_state(_context)
        if abort_state["aborted"]:
            return {"ok": False, "status": "cancelled", "error": "任务已停止，本次操作未执行。"}
        process = run_in_sandbox(
            [python_exe, "-u", "-X", "utf8", temp_path],
            cwd=workspace_dir,
            skill_id="python-runner",
            shell_kind="exec",
            text=False,
            extra_env={"COWORK_VISUALIZATION_DIR": visualization_dir} if visualization_dir else None,
        )
        return process_text(capture_process(process, _context), 'Stderr:')
    except FileNotFoundError:
        return "Error: Executable not found. If you are trying to run a command (like 'ls', 'git'), ensure it is installed and in the system PATH."
    except Exception as e:
        return f"Error executing code: {str(e)}"
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
                pass
