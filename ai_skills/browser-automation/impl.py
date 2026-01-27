import sys
import os
import time
import json
from core.env_utils import ensure_package_installed

# Lazy Loaders
def get_uiautomation():
    if sys.platform != "win32":
        raise ImportError("uiautomation is only supported on Windows")
    ensure_package_installed("uiautomation")
    try:
        import uiautomation as auto
        return auto
    except ImportError as e:
        import site
        info = f"Import failed after install.\nSys.path: {sys.path}\nUserSite: {site.getusersitepackages()}\nError: {e}"
        print(info)
        raise ImportError(f"Failed to load uiautomation: {e}") from e

def get_playwright_sync():
    ensure_package_installed("playwright")
    # Playwright also needs browser binaries (handled by visit_and_screenshot auto-install)
    from playwright.sync_api import sync_playwright
    return sync_playwright

def _install_playwright_browsers():
    """
    Install Playwright browsers (Chromium) if missing.
    """
    try:
        from core.env_utils import get_python_executable
        import subprocess
        python_exe = get_python_executable()
        
        # Suppress output window on Windows
        startupinfo = None
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
        subprocess.check_call(
            [python_exe, "-m", "playwright", "install", "chromium"],
            startupinfo=startupinfo
        )
        return True
    except Exception as e:
        print(f"Error installing browsers: {e}")
        return False

def get_active_tab_info():
    """
    Get the URL and Title of the currently active browser window.
    Works best on Windows with Chrome/Edge.
    """
    if sys.platform != 'win32':
        return "Error: Active tab detection is currently only supported on Windows."

    try:
        auto = get_uiautomation()
        
        # 1. Get Foreground Window
        window = auto.GetForegroundControl().GetTopLevelControl()
        if not window:
            return "Error: No active window found."
            
        # 2. Check if it is a browser
        class_name = window.ClassName
        browser_type = None
        
        if "Chrome_WidgetWin_1" in class_name:
            browser_type = "Chrome/Edge"
        elif "MozillaWindowClass" in class_name:
            browser_type = "Firefox"
        else:
            return f"Info: Active window '{window.Name}' is not a supported browser (Class: {class_name})."
            
        # 3. Find Address Bar (Edit Control)
        # This is heuristic and may break with browser updates
        # Chrome/Edge: Usually an Edit control named "Address and search bar" or similar
        # We search for an Edit control that contains "http" or is the address bar
        
        # Strategy: Search for the Address Bar element
        address_control = None
        
        if browser_type == "Chrome/Edge":
            # Common names for address bar
            address_control = window.EditControl(Name="Address and search bar") 
            if not address_control.Exists():
                # Fallback: Search all edit controls
                for edit in window.GetChildren():
                    if edit.ControlTypeName == "EditControl":
                        # Usually the first one or one with a URL
                        address_control = edit
                        break
        
        elif browser_type == "Firefox":
            address_control = window.EditControl(Name="Search with Google or enter address")
            if not address_control.Exists():
                 address_control = window.EditControl(searchDepth=2) # Firefox structure is deeper

        url = "Unknown"
        if address_control and address_control.Exists():
            # Sometimes ValuePattern is needed, sometimes LegacyIAccessiblePattern
            try:
                url = address_control.GetValuePattern().Value
            except:
                # Try Legacy pattern
                url = address_control.Name 
                
            # If URL doesn't start with http, it might be just the search term or "google.com"
            if url and not url.startswith("http"):
                url = "https://" + url # Assumption
                
        return json.dumps({
            "app": browser_type,
            "title": window.Name,
            "url": url
        }, indent=2)

    except Exception as e:
        return f"Error getting tab info: {str(e)}"

def visit_and_screenshot(url, workspace_dir=None):
    """
    Visit a URL and take a screenshot using Playwright.
    """
    if not workspace_dir:
        workspace_dir = os.getcwd()

    try:
        playwright_sync = get_playwright_sync()
        
        with playwright_sync() as p:
            # Launch browser (try chromium)
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                msg = str(e)
                if "Executable doesn't exist" in msg or "playwright install" in msg.lower():
                     # Attempt to install browsers and retry
                     print("Playwright browsers missing. Installing...")
                     if _install_playwright_browsers():
                         try:
                             browser = p.chromium.launch(headless=True)
                         except Exception as e2:
                             return f"Error: Failed to launch browser even after install attempt. Details: {str(e2)}"
                     else:
                         return f"Error: Failed to install Playwright browsers. Details: {msg}"
                else:
                    return f"Error: Failed to launch browser. Details: {msg}"
                
            page = browser.new_page()
            page.goto(url)
            
            # Save screenshot to workspace
            filename = f"screenshot_{int(time.time())}.png"
            filepath = os.path.join(workspace_dir, "images", filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            page.screenshot(path=filepath)
            browser.close()
            
            return f"Success: Screenshot saved to {filepath}"
            
    except Exception as e:
        return f"Error using Playwright: {str(e)}"
