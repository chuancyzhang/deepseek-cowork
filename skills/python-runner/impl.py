import sys
import subprocess
import tempfile
import os
import ast
import shutil
from core.env_utils import get_python_executable

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
        # Run subprocess
        result = subprocess.run(
            [python_exe, temp_path],
            capture_output=True,
            text=True,
            cwd=workspace_dir,
            encoding='utf-8',
            errors='replace',
            timeout=30 # 30s timeout
        )
        
        output = result.stdout
        if result.stderr:
            output += f"\nStderr: {result.stderr}"
            
        return output if output.strip() else "(No output)"
        
    except subprocess.TimeoutExpired:
        return "Error: Execution timed out (30s)."
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
