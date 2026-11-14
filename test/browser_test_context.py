"""Isolated, real backend authorization for browser adapter fixtures."""
import json
from pathlib import Path
import tempfile
from core.config_manager import ConfigManager
from core.execution_authorization import ExecutionAuthorization, bind_authorization
from core.browser_backend import bind_run, finish_run, browser_operation, EXTERNAL


def browser_config(directory):
    config = ConfigManager.__new__(ConfigManager)
    config.data_dir = str(directory)
    config.config_path = str(Path(directory) / "config.json")
    config.config = {}
    Path(config.config_path).write_text(json.dumps(config.config), encoding="utf-8")
    return config


def authorize_browser_fixture(case):
    temporary = tempfile.TemporaryDirectory()
    case.addCleanup(temporary.cleanup)
    config = browser_config(temporary.name)
    auth = ExecutionAuthorization("chat-a", "test-run", True, "")
    bind_run(config, auth)
    context = {"execution_authorization": auth}
    with browser_operation(context, EXTERNAL) as (coordinator, _):
        coordinator.track_session(auth.session_id, auth.run_id, "abcd")
    binding = bind_authorization(auth)
    binding.__enter__()
    def cleanup():
        if coordinator.run_active(auth.session_id, auth.run_id):
            coordinator.track_session(auth.session_id, auth.run_id, "abcd", stopped=True)
        finish_run(auth)
        binding.__exit__(None, None, None)
    case.addCleanup(cleanup)
    return auth
