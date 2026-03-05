import sys
import subprocess
import tempfile
import os
import ast
import shutil
import locale
from PySide6.QtCore import QObject, Qt
from core.env_utils import get_python_executable, ensure_package_installed

def install_package(package_name, import_name=None):
    """
    Install a Python package and hot-reload it.
    
    Args:
        package_name (str): The pip package name.
        import_name (str, optional): The import module name.
    """
    try:
        ensure_package_installed(package_name, import_name)
        return f"Successfully installed and loaded '{package_name}'."
    except Exception as e:
        return f"Failed to install '{package_name}': {e}"

class SecurityError(Exception):
    pass

def validate_code_safety(code, allowed_dir, god_mode=False):
    """AST static analysis for code safety"""
    if god_mode:
        return True

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SecurityError(f"Syntax Error: {e}")

    allowed_dir = os.path.abspath(allowed_dir).lower()
    
    # Dangerous modules that require God Mode
    dangerous_modules = {'subprocess', 'winreg', 'ctypes'}

    for node in ast.walk(tree):
        # 1. Check for dangerous imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split('.')[0] in dangerous_modules:
                     raise SecurityError(f"Security Alert: Import of restricted module '{alias.name}' is not allowed in Standard Mode. Please enable God Mode to use it.")
        
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split('.')[0] in dangerous_modules:
                 raise SecurityError(f"Security Alert: Import from restricted module '{node.module}' is not allowed in Standard Mode. Please enable God Mode to use it.")

        # 2. Check Path Traversal in strings
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if ".." in val:
                 raise SecurityError(f"Security Alert: Path traversal '..' detected in string: '{val}'")
            if os.path.isabs(val):
                abs_val = os.path.abspath(val).lower()
                if not abs_val.startswith(allowed_dir):
                     raise SecurityError(f"Security Alert: Unauthorized absolute path access: '{val}'")
    return True
 
def _init_abort_state(context):
    state = {"aborted": False, "bridge": None}
    if not context:
        return state
    abort_signal = context.get("abort_signal")
    if not abort_signal:
        return state
    class SignalBridge(QObject):
        def __init__(self, state_ref):
            super().__init__()
            self.state_ref = state_ref
        def trigger(self):
            self.state_ref["aborted"] = True
    bridge = SignalBridge(state)
    abort_signal.connect(bridge.trigger, Qt.DirectConnection)
    state["bridge"] = bridge
    return state

def _decode_output(raw):
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
    best_text = None
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
            best_score = score
            best_text = text
    if best_text is not None:
        return best_text
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return str(raw)

def run_python_code(workspace_dir, code, _context=None):
    """
    Execute Python code in the workspace.
    
    Args:
        workspace_dir (str): Root workspace directory.
        code (str): Python code to execute.
    """
    if not workspace_dir:
        return "Error: Workspace not selected."
        
    god_mode = False
    if _context and 'config_manager' in _context:
        god_mode = _context['config_manager'].get_god_mode()

    try:
        validate_code_safety(code, workspace_dir, god_mode=god_mode)
    except SecurityError as e:
        return f"Error: {str(e)}"

    # Create temp file
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
            f.write(code)
            temp_path = f.name
    except Exception as e:
        return f"Error creating temp file: {e}"

    # Determine python executable
    python_exe = get_python_executable()
    
    try:
        abort_state = _init_abort_state(_context)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        process = subprocess.Popen(
            [python_exe, "-X", "utf8", temp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=False,
            cwd=workspace_dir,
            env=env
        )
        while True:
            if abort_state["aborted"]:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
                return "Error: Execution aborted by user."
            try:
                output_raw, error_raw = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        output = _decode_output(output_raw)
        error = _decode_output(error_raw)
        output = output or ""
        if error:
            output += f"\nStderr: {error}"
        return output if output.strip() else "(No output)"
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
