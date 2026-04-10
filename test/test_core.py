import unittest
import os
import sys
import tempfile
import shutil
import threading
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_manager import ConfigManager
from core.skill_manager import SkillManager
from core.interaction import InteractionBridge
from core import env_utils
from core import sandbox_runtime
from core.daemon import DaemonState
from core.chat_storage import ChatStorage
from core.im_session_key import build_im_session_key, parse_im_session_key, resolve_date_key

class TestConfigManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = os.path.join(self.temp_dir, "config.json")
        # Mock sys.executable to control config path logic if needed, 
        # but ConfigManager logic is complex regarding paths.
        # For simplicity, we just test basic dict operations if we can bypass load.
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_set_get_config(self):
        # We need to patch where ConfigManager looks for files or just test the dict logic
        cm = ConfigManager()
        cm.config = {} # Reset
        cm.set("api_key", "sk-test")
        self.assertEqual(cm.get("api_key"), "sk-test")

class TestSkillManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.temp_dir, "skills")
        os.makedirs(self.skills_dir)
        
    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_load_skills(self):
        # Create a dummy skill
        skill_name = "test-skill"
        skill_path = os.path.join(self.skills_dir, skill_name)
        os.makedirs(skill_path)
        
        with open(os.path.join(skill_path, "SKILL.md"), "w") as f:
            f.write("---\nname: test-skill\n---\nTest skill description.")
            
        with open(os.path.join(skill_path, "impl.py"), "w") as f:
            f.write("def test_func():\n    return 'hello'")
            
        # Patch the skills_dirs detection
        with patch.object(SkillManager, '__init__', return_value=None) as mock_init:
            sm = SkillManager()
            sm.skills_dirs = [self.skills_dir]
            sm.tools = {}
            sm.tool_definitions = []
            sm.skill_prompts = []
            sm.config_manager = None
            
            # Call load_skills directly
            SkillManager.load_skills(sm)
            
            self.assertIn("test_func", sm.tools)
            self.assertEqual(sm.tools["test_func"](), "hello")

class TestInteractionBridge(unittest.TestCase):
    def test_bridge_singleton(self):
        from core.interaction import bridge
        self.assertIsInstance(bridge, InteractionBridge)

class TestEnvUtils(unittest.TestCase):
    def tearDown(self):
        sandbox_runtime._RUNTIME_CACHE = None

    def test_get_python_executable_returns_empty_when_unavailable(self):
        with patch.object(env_utils.sys, "frozen", True, create=True), \
             patch.object(env_utils.sys, "executable", r"C:\app\deepseek-cowork.exe"), \
             patch.object(env_utils.sys, "exec_prefix", r"C:\app"), \
             patch.object(env_utils.sys, "base_prefix", r"C:\app", create=True), \
             patch("core.env_utils.os.path.isfile", return_value=False), \
             patch("core.env_utils.shutil.which", return_value=None), \
             patch("core.env_utils.os.getenv", return_value=""):
            self.assertEqual(env_utils.get_python_executable(), "")

    def test_get_python_executable_frozen_does_not_fallback_to_system_python(self):
        with patch.object(env_utils.sys, "frozen", True, create=True), \
             patch.object(env_utils.sys, "executable", r"C:\app\deepseek-cowork.exe"), \
             patch.object(env_utils.sys, "exec_prefix", r"C:\Python311"), \
             patch.object(env_utils.sys, "base_prefix", r"C:\Python311", create=True), \
             patch("core.env_utils.os.path.isfile", return_value=False), \
             patch("core.env_utils.shutil.which", return_value=r"C:\Python311\python.exe"), \
             patch("core.env_utils.os.getenv", return_value=""):
            self.assertEqual(env_utils.get_python_executable(), "")

    def test_runtime_snapshot_resolves_bundled_python_node_and_bash(self):
        temp_dir = tempfile.mkdtemp()
        try:
            python_dir = os.path.join(temp_dir, "python_env")
            node_dir = os.path.join(temp_dir, "node_env")
            bash_dir = os.path.join(temp_dir, "git_bash_env", "bin")
            os.makedirs(python_dir, exist_ok=True)
            os.makedirs(node_dir, exist_ok=True)
            os.makedirs(bash_dir, exist_ok=True)
            python_exe = os.path.join(python_dir, "python.exe")
            node_exe = os.path.join(node_dir, "node.exe")
            bash_exe = os.path.join(bash_dir, "bash.exe")
            for path in (python_exe, node_exe, bash_exe):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("")
            sandbox_runtime._RUNTIME_CACHE = None
            with patch("core.sandbox_runtime.get_base_dir", return_value=temp_dir), \
                 patch("core.sandbox_runtime.get_app_data_dir", return_value=os.path.join(temp_dir, "data")), \
                 patch("core.sandbox_runtime._copy_runtime_dir", side_effect=lambda source, _name: source), \
                 patch.object(sandbox_runtime.sys, "frozen", True, create=True), \
                 patch.object(env_utils.sys, "frozen", True, create=True):
                snapshot = env_utils.get_runtime_snapshot()
            self.assertEqual(snapshot["python"]["path"], python_exe)
            self.assertEqual(snapshot["node"]["path"], node_exe)
            self.assertEqual(snapshot["bash"]["path"], bash_exe)
            self.assertTrue(snapshot["python"]["available"])
            self.assertTrue(snapshot["node"]["available"])
            self.assertTrue(snapshot["bash"]["available"])
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_build_sandbox_env_adds_skill_dependency_paths(self):
        temp_dir = tempfile.mkdtemp()
        try:
            sandbox_runtime._RUNTIME_CACHE = None
            with patch("core.sandbox_runtime.get_base_dir", return_value=temp_dir), \
                 patch("core.sandbox_runtime.get_app_data_dir", return_value=os.path.join(temp_dir, "data")):
                env = sandbox_runtime.build_sandbox_env(workspace_dir=temp_dir, skill_id="demo-skill")
            self.assertIn(os.path.join("demo-skill", "python", "site-packages"), env["PYTHONPATH"])
            self.assertIn(os.path.join("demo-skill", "node", "node_modules"), env["NODE_PATH"])
            self.assertEqual(env["COWORK_WORKSPACE_DIR"], os.path.abspath(temp_dir))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_ensure_package_installed_reports_missing_runtime(self):
        env_utils._INSTALL_FAILED.clear()
        with patch.object(env_utils, "get_python_executable", return_value=""), \
             patch.object(env_utils.importlib, "import_module", side_effect=ImportError()):
            with self.assertRaises(RuntimeError) as cm:
                env_utils.ensure_package_installed("openpyxl")
            self.assertIn("bundled Python runtime is missing", str(cm.exception))

class _DaemonConfigStub:
    def __init__(self, history_dir):
        self._history_dir = history_dir
    def get_chat_history_dir(self):
        os.makedirs(self._history_dir, exist_ok=True)
        return self._history_dir
    def get(self, key, default=None):
        return default

class TestDaemonConfirmation(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state = DaemonState(_DaemonConfigStub(self.temp_dir))
    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    def test_wait_for_confirmation_timeout_returns_none(self):
        confirm_id = "c-timeout"
        self.state.create_confirmation(confirm_id, "s1")
        result = self.state.wait_for_confirmation(confirm_id, timeout=0.01)
        self.assertIsNone(result)
        self.assertNotIn(confirm_id, self.state.pending_confirmations)
    def test_wait_for_confirmation_resolved_returns_value(self):
        confirm_id = "c-ok"
        self.state.create_confirmation(confirm_id, "s1")
        timer = threading.Timer(0.01, lambda: self.state.resolve_confirmation(confirm_id, True))
        timer.start()
        try:
            result = self.state.wait_for_confirmation(confirm_id, timeout=1)
        finally:
            timer.cancel()
        self.assertTrue(result)
    def test_is_context_overflow_error(self):
        self.assertTrue(self.state._is_context_overflow_error({"error": "maximum context length exceeded"}))
        self.assertFalse(self.state._is_context_overflow_error({"error": "network timeout"}))

class TestImSessionKey(unittest.TestCase):
    def test_build_and_parse_im_session_key(self):
        key = build_im_session_key("u1", "c1", "2026-03-06")
        parsed = parse_im_session_key(key)
        self.assertEqual(parsed["im_user_id"], "u1")
        self.assertEqual(parsed["chat_id"], "c1")
        self.assertEqual(parsed["summary_date"], "2026-03-06")
    def test_resolve_date_key_from_millis_timestamp(self):
        date_key = resolve_date_key("1710000000000")
        self.assertRegex(date_key, r"^\d{4}-\d{2}-\d{2}$")

class TestImDailySummaryStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "chat_history.sqlite")
        self.storage = ChatStorage(self.db_path)
    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    def test_upsert_and_get_im_daily_summary(self):
        self.storage.upsert_im_daily_summary(
            "feishu",
            "user-a",
            "chat-a",
            "2026-03-06",
            "conv-1",
            "summary-a",
            12,
            token_estimate=30,
        )
        row = self.storage.get_im_daily_summary("feishu", "user-a", "chat-a", "2026-03-06")
        self.assertIsNotNone(row)
        self.assertEqual(row["summary_text"], "summary-a")
        self.assertEqual(row["source_message_upto_pos"], 12)
        self.storage.upsert_im_daily_summary(
            "feishu",
            "user-a",
            "chat-a",
            "2026-03-06",
            "conv-1",
            "summary-b",
            20,
            token_estimate=42,
        )
        row2 = self.storage.get_im_daily_summary("feishu", "user-a", "chat-a", "2026-03-06")
        self.assertEqual(row2["summary_text"], "summary-b")
        self.assertEqual(row2["source_message_upto_pos"], 20)

if __name__ == "__main__":
    unittest.main()
