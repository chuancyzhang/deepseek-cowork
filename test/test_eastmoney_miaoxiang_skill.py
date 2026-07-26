import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from core.skill_manager import SkillManager


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_ROOT = os.path.join(REPO_ROOT, "ai_skills", "eastmoney-miaoxiang")


def load_module(name, relative_path):
    module_path = os.path.join(SKILL_ROOT, relative_path)
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigStub:
    def __init__(self, values=None, enable_target=True):
        self.values = values or {}
        self.enable_target = enable_target

    def is_skill_enabled(self, _skill_name, default_enabled=True):
        if self.enable_target and _skill_name == "eastmoney-miaoxiang":
            return True
        return default_enabled

    def get_mcp_servers(self):
        return []

    def get(self, _key, default=None):
        return default

    def get_skill_config(self, skill_name):
        return dict(self.values.get(skill_name, {}))


class TestEastmoneyMiaoxiangSkill(unittest.TestCase):
    def setUp(self):
        self.workspace_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.workspace_dir, ignore_errors=True)

    def build_manager(self, values=None):
        manager = SkillManager(
            workspace_dir=self.workspace_dir,
            config_manager=ConfigStub(values),
            auto_load=False,
            load_mcp_tools=False,
        )
        manager.skills_dirs = [os.path.join(REPO_ROOT, "ai_skills")]
        manager.load_skills(load_mcp_tools=False)
        return manager

    def test_manifest_registers_one_default_off_bundle_with_six_scripts(self):
        manager = self.build_manager()
        record = manager.skill_records["eastmoney-miaoxiang"]
        spec = record["spec"]

        self.assertEqual(spec["source_type"], "bundled_plugin")
        self.assertFalse(spec["default_enabled"])
        self.assertEqual(
            {entry["name"] for entry in spec["script_entries"]},
            {"mx_data", "mx_search", "mx_xuangu", "mx_zixuan", "mx_moni", "mx_poster"},
        )
        self.assertEqual(len(spec["references"]), 7)
        self.assertEqual(len(spec["config_fields"]), 2)
        self.assertEqual(spec["config_fields"][0]["env"], "MX_APIKEY")
        self.assertEqual(spec["config_fields"][1]["env"], "MX_API_URL")

        validation = manager.validate_skill("eastmoney-miaoxiang")
        self.assertTrue(validation["ok"], validation["issues"])

        catalog_manager = SkillManager(
            workspace_dir=self.workspace_dir,
            config_manager=ConfigStub(enable_target=False),
            auto_load=False,
            load_mcp_tools=False,
        )
        catalog_manager.skills_dirs = [os.path.join(REPO_ROOT, "ai_skills")]
        catalog_entry = next(
            item for item in catalog_manager.get_all_skills()
            if item["name"] == "eastmoney-miaoxiang"
        )
        self.assertFalse(catalog_entry["enabled"])
        self.assertEqual(catalog_entry["type"], "bundled_plugin")

    def test_one_saved_config_builds_shared_environment_and_missing_key_fails(self):
        manager = self.build_manager(
            {
                "eastmoney-miaoxiang": {
                    "MX_APIKEY": "secret-test-key",
                }
            }
        )
        env = manager.build_skill_config_env("eastmoney-miaoxiang")
        self.assertEqual(env["MX_APIKEY"], "secret-test-key")
        self.assertEqual(env["MX_API_URL"], "https://mkapi2.dfcfs.com/finskillshub")

        missing_manager = self.build_manager()
        with self.assertRaisesRegex(ValueError, "MX_APIKEY"):
            missing_manager.build_skill_config_env("eastmoney-miaoxiang")

    def test_runtime_output_defaults_to_active_workspace_and_requires_explicit_context(self):
        runtime = load_module("eastmoney_runtime_support_test", "runtime_support.py")
        with patch.dict(
            os.environ,
            {
                "COWORK_WORKSPACE_DIR": self.workspace_dir,
                "MX_API_URL": "https://example.test/api",
            },
            clear=False,
        ):
            output_dir = runtime.resolve_output_dir()
            self.assertEqual(output_dir, (Path(self.workspace_dir) / "mx_data" / "output").resolve())
            self.assertEqual(runtime.configured_api_url(), "https://example.test/api")

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "COWORK_WORKSPACE_DIR"):
                runtime.resolve_output_dir()
            with self.assertRaisesRegex(RuntimeError, "MX_API_URL"):
                runtime.configured_api_url()

    def test_adapted_scripts_remove_implicit_credentials_fixed_paths_and_curl(self):
        python_files = list(Path(SKILL_ROOT).rglob("*.py"))
        combined = "\n".join(path.read_text(encoding="utf-8") for path in python_files)

        self.assertNotIn("/root/.openclaw", combined)
        self.assertNotIn('".env"', combined)
        self.assertNotIn("'curl'", combined)
        self.assertNotIn("subprocess.run", combined)
        self.assertIn("COWORK_WORKSPACE_DIR", combined)

    def test_poster_post_does_not_accept_or_trigger_automatic_interaction(self):
        poster = load_module("eastmoney_mx_poster_test", os.path.join("mx-poster", "mx_poster.py"))
        args = poster.build_parser().parse_args(
            ["post", "--title", "测试标题", "--text", "<p>测试正文</p>"]
        )

        self.assertEqual(args.command, "post")
        self.assertFalse(hasattr(args, "reply_text"))
        self.assertNotIn("run_auto_interaction", Path(poster.__file__).read_text(encoding="utf-8"))
        with self.assertRaisesRegex(RuntimeError, "HTTP 200"):
            poster.MXPoster.parse_response_text("not-json", 200, ok=True)

    def test_data_api_errors_and_business_codes_are_explicit_and_secret_safe(self):
        data_module = load_module("eastmoney_mx_data_test", os.path.join("mx-data", "mx_data.py"))

        class Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"status": 113, "code": 113, "message": "今日调用次数已达上限"}

        stderr = io.StringIO()
        with patch.dict(
            os.environ,
            {
                "MX_APIKEY": "never-log-this-key",
                "MX_API_URL": "https://example.test/finskillshub",
            },
            clear=False,
        ), patch.object(data_module.requests, "post", return_value=Response()) as request_mock, redirect_stderr(stderr):
            result = data_module.MXData().query("测试查询")

        self.assertEqual(result["code"], 113)
        self.assertEqual(
            request_mock.call_args.args[0],
            "https://example.test/finskillshub/api/claw/query",
        )
        self.assertIn('"error_code": "113"', stderr.getvalue())
        self.assertNotIn("never-log-this-key", stderr.getvalue())
        _tables, _conditions, _rows, error = data_module.MXData.parse_result(
            {"status": 0, "data": {"data": {"searchDataResultDTO": {}}}}
        )
        self.assertIn("dataTableDTOList", error)

        with patch.dict(
            os.environ,
            {
                "MX_APIKEY": "never-log-this-key",
                "MX_API_URL": "https://example.test/finskillshub",
            },
            clear=False,
        ), patch.object(
            data_module.requests,
            "post",
            side_effect=data_module.requests.Timeout("timed out"),
        ), self.assertRaises(data_module.requests.Timeout):
            data_module.MXData().query("超时查询")

        class UnauthorizedResponse(Response):
            status_code = 401

            def raise_for_status(self):
                raise data_module.requests.HTTPError("401 Unauthorized", response=self)

        with patch.dict(
            os.environ,
            {
                "MX_APIKEY": "never-log-this-key",
                "MX_API_URL": "https://example.test/finskillshub",
            },
            clear=False,
        ), patch.object(
            data_module.requests,
            "post",
            return_value=UnauthorizedResponse(),
        ), self.assertRaises(data_module.requests.HTTPError):
            data_module.MXData().query("鉴权查询")

    def test_source_document_records_all_official_archives_and_no_license_claim(self):
        source = Path(SKILL_ROOT, "SOURCE.md").read_text(encoding="utf-8")
        manifest = json.loads(Path(SKILL_ROOT, "skill.json").read_text(encoding="utf-8"))

        for name in ("mx-data", "mx-search", "mx-xuangu", "mx-zixuan", "mx-moni", "mx-poster"):
            self.assertIn(name, source)
        self.assertIn("did not include a license file", source)
        self.assertNotIn("license", manifest)


if __name__ == "__main__":
    unittest.main()
