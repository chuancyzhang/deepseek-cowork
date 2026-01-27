import sys
import subprocess
import os
from core.env_utils import get_python_executable, ensure_package_installed

def download_video(url, output_dir=None):
    """
    Download a video from YouTube or other sites using yt-dlp.
    Automatically installs yt-dlp if missing.

    Args:
        url (str): The URL of the video or playlist.
        output_dir (str, optional): Directory to save the video. Defaults to 'downloads' in current workspace.
    
    Returns:
        str: Result message indicating success or failure.
    """
    # 1. Dependency Check & Auto-Install
    try:
        # Check for yt_dlp (package name uses underscore)
        ensure_package_installed("yt-dlp", "yt_dlp")
        # We don't import here because in frozen mode we can't import newly installed packages.
        # import yt_dlp 
    except Exception as e:
        return f"Error: Failed to install yt-dlp. {str(e)}"

    # 2. Setup Output Directory
    if not output_dir:
        # Get the workspace root from where the script is likely running, or just use current cwd
        output_dir = os.path.join(os.getcwd(), "downloads")
    
    if not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
        except OSError as e:
            return f"Error: Could not create output directory '{output_dir}'. {str(e)}"

    # 3. Execute Download via Subprocess
    # This ensures it works even in frozen mode where we can't dynamically import new packages.
    try:
        python_exe = get_python_executable()
        
        # Construct command
        # yt-dlp CLI arguments
        cmd = [
            python_exe, "-m", "yt_dlp",
            url,
            "-o", os.path.join(output_dir, "%(title)s.%(ext)s"),
            "--format", "best",
            "--no-warnings",
            "--ignore-errors" # Keep going if one video in playlist fails
        ]
        
        # Run subprocess
        # Capture stdout/stderr
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        
        if process.returncode == 0:
            # Try to parse output for filenames? 
            # Or just return success.
            # yt-dlp prints "[download] Destination: ..."
            return f"Success: Download completed.\nOutput: {process.stdout[-500:]}" # Return last 500 chars
        else:
            return f"Error running yt-dlp (Exit Code {process.returncode}):\n{process.stderr}"
                
    except Exception as e:
        return f"Error during download execution: {str(e)}"
