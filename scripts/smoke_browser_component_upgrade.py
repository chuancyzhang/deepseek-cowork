"""Validate the bundled Windows CLI without connecting to the user's browser."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import browser_skill_component as component


def main(foreground=False):
    with tempfile.TemporaryDirectory(prefix="cowork-browser-upgrade-") as directory:
        root = Path(directory)
        artifact = component._bundled_artifact_status("cli")
        assert artifact["available"], artifact
        assert component._file_sha256(artifact["path"]) == component.BROWSER_SKILL_SHA256
        component._safe_extract_zip(artifact["path"], str(root / "cli"))
        executable = str(next((root / "cli").rglob("bsk.exe")))
        app = root / "old-app"
        app.mkdir()
        previous = os.getcwd()
        with patch.dict(os.environ, {"BSK_HOME": str(root / "bsk-home"), "LOCALAPPDATA": str(root / "local")}), \
                patch.object(component, "get_app_data_dir", return_value=str(root / "appdata")):
            def run(*args):
                result = component._run_bsk_executable(executable, ["--json", "--quiet", *args], timeout=20)
                assert result["returncode"] == 0, result
                return result
            daemon = None
            try:
                os.chdir(app)
                version = run("--version")["stdout"]
                # Port zero selects a fresh port; existing Chrome/Edge extensions
                # keep using their configured port and cannot attach to this test.
                if foreground:
                    daemon = component.popen_external_program(
                        [executable, "daemon", "start", "--foreground", "--port", "0", "--daemon-idle", "30s"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        **component._browser_process_options(),
                    )
                    deadline = time.monotonic() + 10
                    while not (root / "bsk-home" / "daemon.json").exists():
                        assert daemon.poll() is None and time.monotonic() < deadline, "test daemon did not start"
                        time.sleep(.05)
                else:
                    run("daemon", "start", "--port", "0", "--daemon-idle", "30s")
                os.chdir(previous)
                app.rename(root / "renamed-app")
                assert json.loads(run("session", "list")["stdout"]) == []
                doctor = component._run_bsk_executable(executable, ["--json", "--quiet", "doctor"], timeout=20)
                classification = component._classify_doctor_result(component._parse_json_output(doctor["stdout"], doctor["stderr"]), doctor["stdout"])
                assert classification["kind"] == "extension_disconnected", classification
                print(json.dumps({"version": version, "startup_mode": "foreground" if foreground else "detached", "directory_rename_while_daemon_alive": True,
                                  "session_list": "compatible", "doctor": classification["kind"]}, ensure_ascii=False))
            finally:
                os.chdir(previous)
                try:
                    run("daemon", "stop")
                finally:
                    if daemon:
                        if daemon.poll() is None:
                            daemon.terminate()
                        daemon.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--foreground", action="store_true", help="Explicit test mode for hosts that prohibit detached Windows jobs")
    main(parser.parse_args().foreground)
