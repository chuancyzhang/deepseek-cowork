import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / "ai_skills" / "wecom-unified"


def load_impl():
    spec = importlib.util.spec_from_file_location("wecom_unified_impl", SKILL_ROOT / "impl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Signal:
    def __init__(self):
        self.events = []

    def emit(self, payload):
        self.events.append(payload)


class TestWecomCapabilityPackage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.impl = load_impl()

    def test_manifest_is_official_optional_and_authorized_by_trusted_provider(self):
        manifest = json.loads((SKILL_ROOT / "skill.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["display_name"], "企业微信办公套件")
        self.assertEqual(manifest["source_type"], "bundled_plugin")
        self.assertFalse(manifest["default_enabled"])
        self.assertEqual(manifest["presentation"]["category"], "docs_knowledge")
        self.assertEqual(manifest["authorization"], {"provider": "wecom_cli", "required": True})
        self.assertEqual(manifest["tool_refs"], ["wecom_reference_read", "wecom_cli"])
        self.assertIn("7865dca", (SKILL_ROOT / "SOURCE.md").read_text(encoding="utf-8"))
        self.assertIn("MIT License", (SKILL_ROOT / "LICENSE").read_text(encoding="utf-8"))

    def test_tools_register_only_after_the_optional_capability_is_enabled(self):
        from core.skill_manager import SkillManager

        disabled = SkillManager(workspace_dir=str(ROOT))
        self.assertEqual(disabled.get_tools_for_skill("wecom-unified"), [])

        class Config:
            def is_skill_enabled(self, skill_name, default_enabled=True):
                return skill_name == "wecom-unified" or default_enabled

            def get_mcp_servers(self):
                return []

            def get(self, _key, default=None):
                return default

        enabled = SkillManager(workspace_dir=str(ROOT), config_manager=Config())
        self.assertEqual(
            enabled.get_tools_for_skill("wecom-unified"),
            ["wecom_reference_read", "wecom_cli"],
        )
        payload = json.loads(enabled.call_tool(
            "wecom_reference_read", {"path": "wecomcli-contact.md"}
        ))
        self.assertEqual(payload["status"], "completed")

    def test_every_reference_is_readable_and_unsafe_paths_are_rejected(self):
        references = sorted((SKILL_ROOT / "references").glob("*.md"))
        self.assertGreater(len(references), 80)
        for reference in references:
            payload = json.loads(self.impl.wecom_reference_read(reference.name))
            self.assertEqual(payload["status"], "completed", reference.name)
            self.assertTrue(payload["content"])
        for path in ("../SKILL.md", "references/../../LICENSE", str(SKILL_ROOT / "SKILL.md"), "wecomcli-contact.txt"):
            payload = json.loads(self.impl.wecom_reference_read(path))
            self.assertEqual(payload["status"], "incomplete", path)
            self.assertEqual(payload["error"]["code"], "wecom_reference_invalid_path")

    def test_dialog_auth_commands_and_shell_strings_are_rejected(self):
        shell_payload = json.loads(self.impl.wecom_cli("wecom-cli identity whoami"))
        self.assertEqual(shell_payload["error"]["code"], "wecom_cli_invalid_request")
        auth_payload = json.loads(self.impl.wecom_cli(["auth", "init"]))
        self.assertEqual(auth_payload["error"]["code"], "wecom_cli_invalid_request")
        self.assertIn("能力商店", auth_payload["error"]["message"])
        prefixed_auth = json.loads(self.impl.wecom_cli(["--verbose", "auth", "init"]))
        self.assertEqual(prefixed_auth["error"]["code"], "wecom_cli_invalid_request")

    def test_unauthorized_call_has_explicit_recovery_path(self):
        with patch.object(
            self.impl,
            "get_wecom_authorization_status",
            return_value={"authorized": False, "state": "unauthorized"},
        ):
            payload = json.loads(self.impl.wecom_cli(["identity", "whoami"]))
        self.assertEqual(payload["error"]["code"], "wecom_not_authorized")
        self.assertIn("能力商店", payload["error"]["message"])

    def test_workspace_boundaries_apply_to_uploads_downloads_and_json_paths(self):
        with tempfile.TemporaryDirectory() as workspace:
            with patch.object(
                self.impl,
                "get_wecom_authorization_status",
                return_value={"authorized": True},
            ), patch.object(self.impl, "run_wecom_cli") as runner:
                outside = os.path.abspath(os.path.join(workspace, "..", "secret.txt"))
                payload = json.loads(self.impl.wecom_cli(
                    ["media", "upload", "--json", json.dumps({"file_path": outside})],
                    workspace_dir=workspace,
                ))
                self.assertEqual(payload["error"]["code"], "wecom_cli_invalid_request")
                runner.assert_not_called()

                output_payload = json.loads(self.impl.wecom_cli(
                    ["contact", "search", "--output", outside],
                    workspace_dir=workspace,
                ))
                self.assertEqual(output_payload["error"]["code"], "wecom_cli_invalid_request")
                runner.assert_not_called()

                no_workspace = json.loads(self.impl.wecom_cli(
                    ["disk", "files", "download", "--json", "{}"],
                    workspace_dir=None,
                ))
                self.assertEqual(no_workspace["error"]["code"], "wecom_cli_invalid_request")
                runner.assert_not_called()

    def test_structured_output_and_diagnostics_do_not_expose_arguments(self):
        signal = _Signal()
        result = {
            "status": "completed",
            "exit_code": 0,
            "stdout": '{"ok":true}\n{"token":"private"}\n',
            "stderr": "",
        }
        with tempfile.TemporaryDirectory() as workspace, patch.object(
            self.impl,
            "get_wecom_authorization_status",
            return_value={"authorized": True},
        ), patch.object(self.impl, "run_wecom_cli", return_value=result) as runner:
            payload = json.loads(self.impl.wecom_cli(
                ["contact", "search", "--json", '{"keyword":"TOP_SECRET"}'],
                workspace_dir=workspace,
                _context={"observability_signal": signal},
            ))
        self.assertEqual(payload["stdout"], result["stdout"])
        runner.assert_called_once()
        serialized_events = json.dumps(signal.events, ensure_ascii=False)
        self.assertNotIn("TOP_SECRET", serialized_events)
        self.assertNotIn("private", serialized_events)
        self.assertEqual([event["status"] for event in signal.events], ["start", "run", "finish"])


class TestWecomRuntimeContract(unittest.TestCase):
    def test_bundled_binary_and_manifest_match_pinned_release(self):
        from core.wecom_capability import WECOM_CLI_SHA256, WECOM_CLI_VERSION, _sha256, wecom_cli_path

        executable = Path(wecom_cli_path())
        manifest = json.loads((ROOT / "resources" / "wecom_cli" / "bundle.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], WECOM_CLI_VERSION)
        self.assertEqual(manifest["executable_sha256"], WECOM_CLI_SHA256)
        self.assertEqual(_sha256(executable), WECOM_CLI_SHA256)

    def test_missing_bundle_never_falls_back_to_path(self):
        import core.wecom_capability as capability

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(capability, "get_resource_dir", return_value=temp_dir):
            capability._VERIFIED_BINARY = None
            with self.assertRaises(capability.WecomCapabilityError) as raised:
                capability.wecom_cli_path()
        self.assertEqual(raised.exception.code, "wecom_cli_manifest_invalid")

    def test_provider_registry_rejects_manifest_commands_and_unknown_ids(self):
        from core.capability_authorization import normalize_authorization_declaration

        self.assertEqual(
            normalize_authorization_declaration({
                "provider": "wecom_cli",
                "required": True,
                "command": ["malware.exe"],
            }),
            {"provider": "wecom_cli", "required": True},
        )
        with self.assertRaisesRegex(ValueError, "不受信任"):
            normalize_authorization_declaration({"provider": "arbitrary_command"})

    def test_verified_connection_cache_is_bound_to_encrypted_credentials(self):
        import core.wecom_capability as capability

        with tempfile.TemporaryDirectory() as config_dir:
            credential = Path(config_dir) / "credentials.enc"
            credential.write_bytes(b"encrypted-v1")
            capability._record_verified_connection(config_dir)
            self.assertTrue(capability._cached_connection_verified(config_dir))
            marker = json.loads(
                (Path(config_dir) / capability.WECOM_CONNECTION_STATE_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(set(marker), {
                "schema", "provider", "credential_fingerprint", "verified_at"
            })
            self.assertNotIn("encrypted-v1", json.dumps(marker))
            credential.write_bytes(b"encrypted-v2")
            self.assertFalse(capability._cached_connection_verified(config_dir))

    def test_runtime_timeout_and_cancel_terminate_the_process_tree(self):
        import core.wecom_capability as capability

        class Process:
            returncode = None

            def poll(self):
                return self.returncode

        process = Process()
        with patch.object(capability, "start_wecom_cli", return_value=process), patch.object(
            capability, "terminate_process_tree"
        ) as terminate, patch.object(capability, "log_wecom_event"):
            with self.assertRaises(capability.WecomCapabilityError) as cancelled:
                capability.run_wecom_cli(
                    ["identity", "whoami"], cwd=str(ROOT), abort_check=lambda: True
                )
            self.assertEqual(cancelled.exception.code, "wecom_cli_cancelled")
            terminate.assert_called_with(process)

        process = Process()
        with patch.object(capability, "start_wecom_cli", return_value=process), patch.object(
            capability, "terminate_process_tree"
        ) as terminate, patch.object(capability, "log_wecom_event"), patch.object(
            capability.time, "monotonic", side_effect=[0.0, 2.0, 2.0]
        ):
            with self.assertRaises(capability.WecomCapabilityError) as timed_out:
                capability.run_wecom_cli(
                    ["identity", "whoami"], cwd=str(ROOT), timeout_seconds=1
                )
            self.assertEqual(timed_out.exception.code, "wecom_cli_timeout")
            terminate.assert_called_with(process)


if __name__ == "__main__":
    unittest.main()
