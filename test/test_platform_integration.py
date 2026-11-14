"""Platform policy must agree across discovery, execution and update UI state."""
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch


class PlatformIntegrationTests(unittest.TestCase):
    def test_disabled_skill_and_vector_tool_cannot_be_discovered_or_executed(self):
        from core.skill_manager import SkillManager
        with tempfile.TemporaryDirectory() as root, patch("core.platform_capabilities.is_windows", return_value=False):
            manager = SkillManager(workspace_dir=root, auto_load=False, load_mcp_tools=False)
            self.assertFalse(manager._is_skill_enabled_for_path("wecom-unified", root))
            manager._register_tool("history-query", "query_history", lambda: "keyword")
            manager._register_tool("history-query", "query_history_vector", lambda: self.fail("must not execute"))
            names = {item["function"]["name"] for item in manager.get_tool_definitions()}
            self.assertIn("query_history", names)
            self.assertNotIn("query_history_vector", names)
            denied = manager.call_tool("query_history_vector", {})
            self.assertEqual(denied["status"], "unsupported_platform")
            self.assertFalse(denied["executed"])
            denied = manager.call_tool("run_skill_script", {"skill_name": "huashu-design"})
            self.assertEqual(denied["status"], "unsupported_platform")
            self.assertFalse(manager.ensure_skill_dependencies_ready("wecom-unified")["ok"])

    def test_non_windows_bootstrap_resolves_directly_to_full_agent(self):
        from bootstrap_plugins import resolve
        with patch("core.platform_capabilities.is_windows", return_value=False):
            self.assertIsNone(resolve({"bootstrap_plugin": "deepseek_flash_minimal"}, "deepseek-chat"))

    def test_browser_policy_rejects_external_even_with_saved_preference(self):
        from core.browser_backend import BrowserBackendCoordinator, BrowserBackendError, run_backend
        with tempfile.TemporaryDirectory() as root:
            class Config:
                config_path = str(Path(root) / "config.json")
            Path(Config.config_path).write_text('{"experimental_browser_automation":{"enabled":false}}')
            with patch("core.browser_backend.is_windows", return_value=False):
                coordinator = BrowserBackendCoordinator(Config())
                self.assertEqual(coordinator.snapshot()["backend"], "internal")
                self.assertEqual(run_backend({"browser_backend": {"backend": "external"}}), "internal")
                with self.assertRaises(BrowserBackendError):
                    coordinator.save(False)
                with self.assertRaises(BrowserBackendError):
                    coordinator.acquire("session", "run", {"backend": "external", "generation": 0}, "external")

    def test_manual_update_download_is_verified_and_never_extracts_or_installs(self):
        from core import updater
        from core.update_coordinator import prepare_online, load_prepared
        from core.update_manifest import release_filename
        with tempfile.TemporaryDirectory() as root:
            name = release_filename("9.0.0", "linux-x64")
            package = Path(root) / "downloads" / name
            data = b"verified deb fixture"
            asset = dict(name=name, browser_download_url="https://example.test/" + name,
                         size=len(data), digest="sha256:" + hashlib.sha256(data).hexdigest())
            release = dict(tag_name="9.0.0", assets=[asset])
            config = dict(public_base_url="", target="linux-x64")
            def download(*args, **kwargs):
                package.parent.mkdir(exist_ok=True)
                package.write_bytes(data)
                return str(package)
            with patch.object(updater, "is_windows", return_value=False), \
                 patch.object(updater, "desktop_target", return_value="linux-x64"), \
                 patch.object(updater, "fetch_latest_release", return_value=release), \
                 patch.object(updater, "download_asset", side_effect=download), \
                 patch.object(updater, "extract_update_zip", side_effect=AssertionError("must not extract")):
                result = prepare_online(root, "5.3.3", False, threading.Event(), lambda *a, **kw: None, source_config=config)
                self.assertTrue(result["manual_install"])
                self.assertNotIn("staged_app_dir", result)
                self.assertTrue(updater.is_cached_asset_valid(asset, package))
                self.assertTrue(load_prepared(root, "5.3.3", source_config=config)["manual_install"])
                package.write_bytes(b"tampered")
                self.assertFalse(updater.is_cached_asset_valid(asset, package))
                self.assertIsNone(load_prepared(root, "5.3.3", source_config=config))
                with self.assertRaises(updater.UpdaterError):
                    updater.select_release_asset({"tag_name": "9.0.0", "assets": [dict(asset, name="deepseek-cowork-v9.0.0.zip")]})


if __name__ == "__main__":
    unittest.main()
