import os
import json
from core.interaction import ask_user

def _is_god_mode(context):
    if context and 'config_manager' in context:
        return context['config_manager'].get_god_mode()
    return False

def list_files(workspace_dir, path=".", _context=None):
    """
    List files in the current workspace directory.
    
    Args:
        workspace_dir (str): The root workspace directory (injected by system).
        path (str): Relative path to list, default is '.'.
    """
    try:
        if not workspace_dir:
            return "Error: Workspace not selected."
        
        god_mode = _is_god_mode(_context)
        
        # Security check
        abs_path = os.path.abspath(os.path.join(workspace_dir, path))
        if not god_mode and not abs_path.startswith(os.path.abspath(workspace_dir)):
             return "Error: Access denied (Path Traversal)."
        
        if not os.path.exists(abs_path):
            return f"Error: Path '{path}' does not exist."
        
        items = os.listdir(abs_path)
        # Filter hidden files
        items = [i for i in items if not i.startswith('.')]
        return json.dumps(items)
    except Exception as e:
        return f"Error: {str(e)}"

def rename_file(workspace_dir, old_path, new_path, _context=None):
    """
    Rename a file or directory.
    
    Args:
        workspace_dir (str): The root workspace directory (injected by system).
        old_path (str): The current relative path of the file/directory.
        new_path (str): The new relative path of the file/directory.
    """
    try:
        if not workspace_dir:
            return "Error: Workspace not selected."
            
        abs_old_path = os.path.abspath(os.path.join(workspace_dir, old_path))
        abs_new_path = os.path.abspath(os.path.join(workspace_dir, new_path))
        abs_workspace = os.path.abspath(workspace_dir)
        
        god_mode = _is_god_mode(_context)

        # Security Checks
        if not god_mode:
            if not abs_old_path.startswith(abs_workspace):
                 return "Error: Access denied (Source Path Traversal)."
            if not abs_new_path.startswith(abs_workspace):
                 return "Error: Access denied (Destination Path Traversal)."
        
        if not os.path.exists(abs_old_path):
            return f"Error: Source '{old_path}' does not exist."
            
        if os.path.exists(abs_new_path):
            return f"Error: Destination '{new_path}' already exists."

        os.rename(abs_old_path, abs_new_path)
        return f"Success: Renamed '{old_path}' to '{new_path}'."
            
    except Exception as e:
        return f"Error: {str(e)}"

def read_file(workspace_dir, path, _context=None):
    """
    Read the content of a file.
    
    Args:
        workspace_dir (str): The root workspace directory (injected by system).
        path (str): Relative path to the file.
    """
    try:
        if not workspace_dir:
            return "Error: Workspace not selected."
            
        abs_path = os.path.abspath(os.path.join(workspace_dir, path))
        god_mode = _is_god_mode(_context)
        
        if not god_mode and not abs_path.startswith(os.path.abspath(workspace_dir)):
             return "Error: Access denied (Path Traversal)."
        
        if not os.path.exists(abs_path):
            return f"Error: File '{path}' does not exist."
            
        if not os.path.isfile(abs_path):
            return f"Error: '{path}' is not a file."
            
        # Limit file size to avoid context overflow (e.g., 50KB)
        # In God Mode, maybe we relax this? Or keep it for context safety. Let's keep it for now.
        if os.path.getsize(abs_path) > 50 * 1024:
             return f"Error: File '{path}' is too large to read directly (max 50KB)."

        with open(abs_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
            
    except Exception as e:
        return f"Error: {str(e)}"

def delete_file(workspace_dir, path, _context=None):
    """
    Delete a file or empty directory.
    
    Args:
        workspace_dir (str): The root workspace directory (injected by system).
        path (str): Relative path to the file.
    """
    try:
        if not workspace_dir:
            return "Error: Workspace not selected."
            
        abs_path = os.path.abspath(os.path.join(workspace_dir, path))
        god_mode = _is_god_mode(_context)
        
        if not god_mode and not abs_path.startswith(os.path.abspath(workspace_dir)):
             return "Error: Access denied (Path Traversal)."
        
        if not os.path.exists(abs_path):
            return f"Error: File '{path}' does not exist."
            
        # Ask for confirmation
        # Strict check for True (Yes button). Any text response or False counts as cancellation for safety.
        if ask_user(f"⚠️ DANGER: Are you sure you want to delete '{path}'?") is not True:
            return "Error: Deletion cancelled by user."

        if os.path.isfile(abs_path):
            os.remove(abs_path)
        elif os.path.isdir(abs_path):
            os.rmdir(abs_path) # Only empty directories
        else:
            return f"Error: Unknown file type for '{path}'."
            
        return f"Success: Deleted '{path}'."
            
    except Exception as e:
        return f"Error: {str(e)}"
