"""Exercise the production BrowserSkill service supervisor in an isolated home."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import browser_skill_component as component


def main(gate=None):
    if gate:
        deadline = time.monotonic() + 20
        while not Path(gate).exists():
            if time.monotonic() > deadline:
                raise RuntimeError("Test parent did not release the startup gate")
            time.sleep(.05)
    with tempfile.TemporaryDirectory(prefix="cowork-browser-service-") as directory:
        root = Path(directory)
        component._safe_extract_zip(component._bundled_artifact_status("cli")["path"], str(root / "cli"))
        executable = str(next((root / "cli").rglob("bsk.exe")))
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        events = []
        exited = threading.Event()
        def log(stage, **fields):
            events.append({"stage": stage, **fields})
            if stage == "service_exit":
                exited.set()
        with patch.dict(os.environ, {"BSK_HOME": str(root / "home"), "LOCALAPPDATA": str(root / "local")}), \
                patch.object(component, "browser_skill_executable", return_value=executable), \
                patch.object(component, "get_app_data_dir", return_value=str(root / "appdata")), \
                patch.object(component, "log_browser_skill_event", side_effect=log):
            # The parent can place us in an explicit non-breakaway Windows job.
            detached = component._run_bsk(["daemon", "start", "--port", str(port)], timeout=12)
            if gate:
                assert detached["returncode"] != 0, detached
                assert "job" in (detached["stdout"] + detached["stderr"]).lower(), detached
            elif detached["returncode"] == 0:
                component._stop_managed_daemon()
            original_cwd = os.getcwd()
            old_app = root / "old-app"
            old_app.mkdir()
            try:
                os.chdir(old_app)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda _: component._ensure_browser_skill_service(port=port), range(2)))
                assert all(result["ready"] for result in results), results
                assert len({result["pid"] for result in results}) == 1, results
                assert len([event for event in events if event["stage"] == "service_start"]) == 1, events
                os.chdir(original_cwd)
                old_app.rename(root / "renamed-app")
                for _ in range(2):
                    state = component._probe_browser_skill_service()
                    assert state["state"] == "ready" and state["pid"] == results[0]["pid"], state
                assert component._ensure_browser_skill_service()["mode"] == "existing"
                assert component._stop_managed_daemon()["stopped"]
                assert exited.wait(5), "supervised child not reaped"
                assert component._probe_browser_skill_service()["state"] == "missing"
                # A normal client never respawns a lost daemon or fake session.
                continuation = component._run_bsk(["--json", "--quiet", "snapshot", "--session", "abcd"], timeout=8)
                assert continuation["returncode"] == 2, continuation
                assert component._probe_browser_skill_service()["state"] == "missing"
                exited.clear()
                restarted = component._ensure_browser_skill_service(port=port)
                assert restarted["ready"], restarted
                assert restarted["pid"] != results[0]["pid"]
                print(json.dumps({"restricted_job": bool(gate), "detached_rejected": detached["returncode"] != 0,
                                  "hosted_start": True, "concurrent_start_single_pid": True,
                                  "cross_command_reuse": True, "directory_rename": True,
                                  "continuation_does_not_restart": True, "explicit_start_after_stop": True}))
            finally:
                os.chdir(original_cwd)
                component._stop_managed_daemon()
                exited.wait(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-gate")
    main(parser.parse_args().start_gate)
