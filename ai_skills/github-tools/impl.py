import os
import shutil
import subprocess
import tempfile
import time

def clone_repository(repo_url, workspace_dir=None):
    """
    Clone a GitHub repository to a temporary directory within the workspace.
    """
    try:
        if workspace_dir:
            temp_dir = os.path.join(workspace_dir, "temp_repos")
            os.makedirs(temp_dir, exist_ok=True)
        else:
            temp_dir = tempfile.mkdtemp()
            
        repo_name = repo_url.rstrip('/').split("/")[-1]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]
        target_dir = os.path.join(temp_dir, repo_name)
        
        max_retries = 3
        last_error = ""
        for attempt in range(1, max_retries + 1):
            if os.path.exists(target_dir):
                try:
                    def on_rm_error(func, path, exc_info):
                        os.chmod(path, 0o777)
                        func(path)
                    shutil.rmtree(target_dir, onerror=on_rm_error)
                except Exception as e:
                    return f"Error cleaning up existing directory '{target_dir}': {str(e)}"
            
            cmd = ["git", "clone", "--depth", "1", repo_url, target_dir]
            if shutil.which("git") is None:
                return "Error: 'git' command not found. Please install Git."
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
                return target_dir
            except subprocess.CalledProcessError as e:
                last_error = e.stderr
                if attempt < max_retries:
                    time.sleep(2 * attempt)
                else:
                    if "github.com" in repo_url and ("Connection was reset" in last_error or "Failed to connect" in last_error):
                        last_error += "\n(Tip: You might need a proxy or VPN to access GitHub. Or try configuring git proxy: 'git config --global http.proxy ...')"
                    return f"Error cloning repository after {max_retries} attempts: {last_error}"
        return f"Error: Failed to clone repository. {last_error}"
    except Exception as e:
        return f"Error: {str(e)}"

def analyze_repository(repo_path):
    """
    Analyze a repository to extract information for skill generation.
    Returns file structure and content of key files (README, requirements, etc.).
    """
    if not os.path.exists(repo_path):
        return "Error: Repository path does not exist."
        
    summary = []
    summary.append(f"# Analysis of {os.path.basename(repo_path)}")
    summary.append("\n## File Structure")
    
    file_list = []
    max_lines = 100
    for root, dirs, files in os.walk(repo_path):
        if '.git' in dirs:
            dirs.remove('.git')
        if '__pycache__' in dirs:
            dirs.remove('__pycache__')
        level = root.replace(repo_path, '').count(os.sep)
        if level > 2:
            continue
        indent = '  ' * level
        file_list.append(f"{indent}{os.path.basename(root)}/")
        subindent = '  ' * (level + 1)
        for f in files:
            if f.startswith('.'):
                continue
            file_list.append(f"{subindent}{f}")
        if len(file_list) > max_lines:
            file_list.append("... (truncated)")
            break
    summary.append("\n".join(file_list))
    
    summary.append("\n## Key Files Content")
    key_files = ['README.md', 'README.rst', 'requirements.txt', 'setup.py', 'pyproject.toml']
    candidates = [f for f in os.listdir(repo_path) if f.endswith('.py') and f not in key_files]
    key_files.extend(candidates[:2])
    for filename in key_files:
        path = os.path.join(repo_path, filename)
        if os.path.exists(path):
            summary.append(f"\n### {filename}")
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(3000)
                    summary.append(content)
                    if len(content) == 3000:
                        summary.append("\n... (truncated)")
            except Exception as e:
                summary.append(f"(Error reading file: {str(e)})")
    return "\n".join(summary)
