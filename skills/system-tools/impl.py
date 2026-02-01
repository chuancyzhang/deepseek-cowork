import os
import subprocess
import re
import fnmatch

def _is_god_mode(context):
    if context and 'config_manager' in context:
        return context['config_manager'].get_god_mode()
    return False

def bash(workspace_dir, command, _context=None):
    """
    Execute a shell command.
    
    Args:
        workspace_dir (str): The current workspace directory.
        command (str): The command to execute.
    """
    try:
        # Check God Mode if strict security is needed, but user requested these as built-in skills.
        # We will assume they are allowed but should be used responsibly.
        
        cwd = workspace_dir if workspace_dir else os.getcwd()
        
        # Use shell=True to allow shell syntax (pipes, redirects, etc.)
        # On Windows, this uses cmd.exe or powershell depending on the environment/comspec
        result = subprocess.run(
            command, 
            shell=True, 
            cwd=cwd, 
            capture_output=True, 
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        output = result.stdout
        if result.stderr:
            if output:
                output += "\n"
            output += f"STDERR:\n{result.stderr}"
            
        return output if output else "(No output)"
    except Exception as e:
        return f"Error executing command: {str(e)}"

def grep(workspace_dir, pattern, path=".", include="*", exclude=None, recursive=True, _context=None):
    """
    Search for a text pattern in files using regex.
    
    Args:
        workspace_dir (str): Root workspace.
        pattern (str): Regex pattern to search.
        path (str): Relative path to start search (default: ".").
        include (str): Glob pattern for files to include (default: "*").
        exclude (str): Glob pattern for files to exclude.
        recursive (bool): Whether to search recursively (default: True).
    """
    if not workspace_dir:
        return "Error: Workspace not selected."
        
    start_dir = os.path.abspath(os.path.join(workspace_dir, path))
    results = []
    
    # Common ignore patterns
    default_excludes = {'.git', '.idea', '__pycache__', 'node_modules', '.venv', 'venv', 'dist', 'build'}
    if exclude:
        exclude_patterns = set(exclude.split(',')) | default_excludes
    else:
        exclude_patterns = default_excludes

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: Invalid regex pattern - {str(e)}"

    match_count = 0
    max_matches = 1000

    try:
        for root, dirs, files in os.walk(start_dir):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in exclude_patterns]
            
            for file in files:
                if file in exclude_patterns:
                    continue
                
                # Check include pattern
                if not fnmatch.fnmatch(file, include):
                    continue
                    
                file_path = os.path.join(root, file)
                
                # Check binary
                try:
                    # Quick check for binary
                    with open(file_path, 'rb') as f:
                        is_binary = b'\0' in f.read(1024)
                    if is_binary:
                        continue
                        
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                        for i, line in enumerate(lines):
                            if regex.search(line):
                                rel_path = os.path.relpath(file_path, workspace_dir)
                                results.append(f"{rel_path}:{i+1}: {line.strip()}")
                                match_count += 1
                                if match_count >= max_matches:
                                    results.append("... (Truncated due to match limit)")
                                    return "\n".join(results)
                                    
                except Exception:
                    continue
            
            if not recursive:
                break
                
        if not results:
            return "No matches found."
            
        return "\n".join(results)
        
    except Exception as e:
        return f"Error: {str(e)}"
