import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from core.sandbox_runtime import get_runtime_executable
from core.skill_manager import SkillManager


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "ai_skills" / "wind-aifinmarket"
EXPECTED_COMMIT = "94c00f94a3b6e8b61ebf375ad9c5cb87da34cd12"
EXPECTED_ENTRIES = {
    "wind_mcp",
    "wind_alice",
    "tushare_query",
    "backtest_evaluate",
    "dcf_validate",
    "position_size",
    "market_environment",
    "theme_detect",
}


def load_impl():
    spec = importlib.util.spec_from_file_location("wind_aifinmarket_impl", SKILL_ROOT / "impl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigStub:
    def is_skill_enabled(self, name, default_enabled=True):
        return name == "wind-aifinmarket" or default_enabled

    def get_mcp_servers(self):
        return []

    def get(self, _key, default=None):
        return default

    def get_skill_config(self, _name):
        return {}


class TestWindAIFinMarketSkill(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.impl = load_impl()

    def test_snapshot_contains_exactly_78_subskills_and_source_commit(self):
        directories = sorted(path.name for path in (SKILL_ROOT / "skills").iterdir() if path.is_dir())
        self.assertEqual(len(directories), 78)
        self.assertTrue(all((SKILL_ROOT / "skills" / name / "SKILL.md").is_file() for name in directories))
        catalog_names = sorted(record["name"] for record in self.impl._catalog())
        self.assertEqual(catalog_names, directories)
        source = (SKILL_ROOT / "SOURCE.md").read_text(encoding="utf-8")
        self.assertIn(EXPECTED_COMMIT, source)
        self.assertIn("all 78 directories", source)
        frozen_names = sorted(
            line[3:-1]
            for line in source.splitlines()
            if line.startswith("- `") and line.endswith("`") and "://" not in line
        )
        self.assertEqual(frozen_names, directories)

    def test_search_supports_chinese_english_and_exact_names(self):
        chinese = json.loads(self.impl.search_wind_subskills("贵州茅台最新股价", 5))
        self.assertTrue(chinese["ok"])
        self.assertEqual(chinese["catalog_size"], 78)
        self.assertEqual(chinese["results"][0]["name"], "wind-mcp-skill")
        exact = json.loads(self.impl.search_wind_subskills("theme-detector", 3))
        self.assertEqual(exact["results"][0]["name"], "theme-detector")
        self.assertEqual(exact["results"][0]["data_source"], "FINVIZ/FMP")
        english = json.loads(self.impl.search_wind_subskills("DCF valuation", 5))
        self.assertTrue(any(item["name"] == "dcf-model" for item in english["results"]))
        capped = json.loads(self.impl.search_wind_subskills("skill", 999))
        self.assertLessEqual(capped["count"], 20)
        minimum = json.loads(self.impl.search_wind_subskills("skill", -5))
        self.assertLessEqual(minimum["count"], 1)

    def test_loader_applies_cowork_rules_and_blocks_path_traversal(self):
        loaded = json.loads(self.impl.load_wind_subskill("wind-mcp-skill"))
        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["reference"], "SKILL.md")
        self.assertIn("Cowork 强制适配规则", loaded["content"])
        reference = json.loads(
            self.impl.load_wind_subskill("wind-mcp-skill", "references/error-codes.json")
        )
        self.assertTrue(reference["ok"])
        self.assertIn("AUTH_ERROR", reference["content"])
        invalid = json.loads(self.impl.load_wind_subskill("wind-mcp-skill", "../SKILL.md"))
        self.assertEqual(invalid["error"], "invalid_reference_path")
        script = json.loads(
            self.impl.load_wind_subskill("wind-mcp-skill", "scripts/cli.mjs")
        )
        self.assertEqual(script["error"], "reference_not_allowed")
        self.assertIn("SKILL.md", script["allowed_references"])
        self.assertIn("without reference", script["recovery"])
        unknown = json.loads(self.impl.load_wind_subskill("not-a-real-skill"))
        self.assertEqual(unknown["error"], "unknown_subskill")

    def test_loader_recovers_from_aggregate_source_reference(self):
        loaded = json.loads(
            self.impl.load_wind_subskill(
                "a-share-primary-theme-identification",
                "SOURCE.md",
            )
        )
        self.assertTrue(loaded["ok"])
        self.assertEqual(loaded["reference"], "SKILL.md")
        self.assertEqual(loaded["requested_reference"], "SOURCE.md")
        self.assertIn("belongs to the aggregate Wind skill", loaded["notice"])
        self.assertIn("# A股市场主线识别", loaded["content"])

    def test_tool_contract_requires_first_call_to_omit_reference(self):
        export = next(
            item for item in self.impl.TOOL_EXPORTS if item["name"] == "load_wind_subskill"
        )
        self.assertIn("omit reference", export["description"])
        self.assertIn(
            "Do not pass SOURCE.md",
            export["parameters"]["properties"]["reference"]["description"],
        )

    def test_tools_are_read_only_and_non_destructive(self):
        exports = {item["name"]: item for item in self.impl.TOOL_EXPORTS}
        self.assertEqual(set(exports), {"search_wind_subskills", "load_wind_subskill"})
        self.assertTrue(all(item["read_only"] for item in exports.values()))
        self.assertTrue(all(not item["destructive"] for item in exports.values()))

    def test_manifest_is_one_default_off_plugin_with_eight_entries(self):
        manager = SkillManager(
            workspace_dir=tempfile.mkdtemp(),
            config_manager=ConfigStub(),
            auto_load=False,
            load_mcp_tools=False,
        )
        manager.skills_dirs = [str(REPO_ROOT / "ai_skills")]
        manager.load_skills(load_mcp_tools=False)
        record = manager.skill_records["wind-aifinmarket"]
        spec = record["spec"]
        self.assertEqual(spec["source_type"], "bundled_plugin")
        self.assertFalse(spec["default_enabled"])
        self.assertEqual({item["name"] for item in spec["script_entries"]}, EXPECTED_ENTRIES)
        self.assertEqual(
            {item["name"] for item in spec["config_fields"]},
            {
                "WIND_API_KEY",
                "WIND_ALICE_API_URL",
                "TUSHARE_TOKEN",
                "FINVIZ_MODE",
                "FINVIZ_API_KEY",
                "FMP_API_KEY",
            },
        )
        self.assertTrue(all(not item["required"] for item in spec["config_fields"]))
        self.assertTrue(manager.validate_skill("wind-aifinmarket")["ok"])

    def test_registered_wrappers_reject_missing_credentials_without_network(self):
        node = get_runtime_executable("node")
        self.assertTrue(node)
        wind = subprocess.run(
            [node, str(SKILL_ROOT / "scripts" / "wind_mcp.mjs"), "call", "stock_data", "x", "{}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={key: value for key, value in os.environ.items() if key != "WIND_API_KEY"},
            timeout=10,
        )
        self.assertNotEqual(wind.returncode, 0)
        self.assertIn("AUTH_ERROR", wind.stderr)
        self.assertIn('"stage":"submit"', wind.stderr)
        self.assertIn('"stage":"error"', wind.stderr)
        alice = subprocess.run(
            [node, str(SKILL_ROOT / "scripts" / "wind_alice.mjs"), "--prompt", "test"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={key: value for key, value in os.environ.items() if key != "WIND_API_KEY"},
            timeout=10,
        )
        self.assertNotEqual(alice.returncode, 0)
        self.assertIn("AUTH_ERROR", alice.stderr)
        tushare = subprocess.run(
            [os.sys.executable, str(SKILL_ROOT / "scripts" / "tushare_query.py"), "daily", "{}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={key: value for key, value in os.environ.items() if key != "TUSHARE_TOKEN"},
            timeout=10,
        )
        self.assertNotEqual(tushare.returncode, 0)
        self.assertIn("AUTH_ERROR", tushare.stdout)
        self.assertIn('"stage": "submit"', tushare.stderr)
        self.assertIn('"stage": "error"', tushare.stderr)

    def test_local_calculation_writes_only_to_active_workspace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = dict(os.environ)
            env["COWORK_WORKSPACE_DIR"] = temp_dir
            result = subprocess.run(
                [
                    os.sys.executable,
                    str(SKILL_ROOT / "scripts" / "backtest_evaluate.py"),
                    "--total-trades",
                    "120",
                    "--win-rate",
                    "55",
                    "--avg-win-pct",
                    "3",
                    "--avg-loss-pct",
                    "1.5",
                    "--max-drawdown-pct",
                    "12",
                    "--years-tested",
                    "5",
                    "--num-parameters",
                    "4",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"stage": "submit"', result.stderr)
            self.assertIn('"stage": "run"', result.stderr)
            self.assertIn('"stage": "finish"', result.stderr)
            self.assertTrue(any(Path(temp_dir).rglob("*")))

    def test_remaining_local_entries_have_help_or_fixture_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = dict(os.environ)
            env["COWORK_WORKSPACE_DIR"] = temp_dir
            cases = [
                ("dcf_validate.py", []),
                ("market_environment.py", []),
                ("position_size.py", ["--help"]),
            ]
            for script_name, arguments in cases:
                with self.subTest(script=script_name):
                    result = subprocess.run(
                        [os.sys.executable, str(SKILL_ROOT / "scripts" / script_name), *arguments],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        env=env,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('"stage": "submit"', result.stderr)
                    self.assertIn('"stage": "finish"', result.stderr)
            escape = subprocess.run(
                [
                    os.sys.executable,
                    str(SKILL_ROOT / "scripts" / "position_size.py"),
                    "--output-dir",
                    "../outside-workspace",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=20,
            )
            self.assertNotEqual(escape.returncode, 0)
            self.assertIn("WorkspacePathEscape", escape.stderr)

    def test_tushare_mock_success_empty_result_and_secret_redaction(self):
        sitecustomize = """
import json
import os
import urllib.request

class Response:
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        return False
    def read(self):
        return os.environ["MOCK_TUSHARE_PAYLOAD"].encode("utf-8")

def urlopen(_request, timeout=0):
    return Response()

urllib.request.urlopen = urlopen
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "sitecustomize.py").write_text(sitecustomize, encoding="utf-8")
            env = dict(os.environ)
            env["PYTHONPATH"] = temp_dir
            env["TUSHARE_TOKEN"] = "super-secret-token"
            env["MOCK_TUSHARE_PAYLOAD"] = json.dumps(
                {"code": 0, "data": {"fields": ["ts_code"], "items": [["000001.SZ"]]}}
            )
            command = [
                os.sys.executable,
                str(SKILL_ROOT / "scripts" / "tushare_query.py"),
                "daily",
                "{}",
            ]
            success = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=20,
            )
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertTrue(json.loads(success.stdout)["ok"])
            self.assertNotIn("super-secret-token", success.stdout + success.stderr)
            self.assertIn('"stage": "run"', success.stderr)
            self.assertIn('"stage": "finish"', success.stderr)

            env["MOCK_TUSHARE_PAYLOAD"] = json.dumps(
                {"code": 0, "data": {"fields": ["ts_code"], "items": []}}
            )
            empty = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=20,
            )
            self.assertNotEqual(empty.returncode, 0)
            self.assertIn("NO_RESULTS", empty.stdout)

    def test_theme_detector_uses_explicit_mode_and_local_fixture(self):
        fixture_html = """
        <table>
          <tr><th>Name</th><th>Perf Week</th><th>Perf Month</th><th>Perf Quart</th></tr>
          <tr><td>Semiconductors</td><td>5.0%</td><td>10.0%</td><td>20.0%</td></tr>
          <tr><td>Utilities</td><td>-2.0%</td><td>-1.0%</td><td>1.0%</td></tr>
        </table>
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = Path(temp_dir) / "finviz.html"
            fixture.write_text(fixture_html, encoding="utf-8")
            env = dict(os.environ)
            env["COWORK_WORKSPACE_DIR"] = temp_dir
            env["FINVIZ_MODE"] = "public"
            result = subprocess.run(
                [
                    os.sys.executable,
                    str(SKILL_ROOT / "scripts" / "theme_detect.py"),
                    "--fixture",
                    str(fixture),
                    "--top",
                    "1",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["source"], "FINVIZ-public")
            self.assertEqual(payload["leaders"][0]["industry"], "Semiconductors")
            self.assertTrue(Path(payload["output_path"]).is_relative_to(Path(temp_dir)))

            env["FINVIZ_MODE"] = "elite"
            env.pop("FINVIZ_API_KEY", None)
            missing_key = subprocess.run(
                [os.sys.executable, str(SKILL_ROOT / "scripts" / "theme_detect.py")],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=20,
            )
            self.assertNotEqual(missing_key.returncode, 0)
            self.assertIn("AUTH_ERROR", missing_key.stdout)

    def test_entire_plugin_has_no_installer_updater_or_user_key_probe(self):
        audited_files = [
            path
            for path in SKILL_ROOT.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".mjs"}
        ]
        combined = "\n".join(
            path.read_text(encoding="utf-8-sig") for path in audited_files
        )
        self.assertNotIn("npx skills", combined)
        self.assertNotIn("skills update", combined)
        self.assertNotIn("setup-key", combined)
        self.assertNotIn("open-portal", combined)
        self.assertNotIn(".wind-aifinmarket", combined)
        self.assertNotIn("USERPROFILE", combined)
        self.assertNotIn("homedir", combined)
        self.assertNotIn("update-check.mjs", combined)
        self.assertFalse(any(SKILL_ROOT.rglob("update-check.mjs")))


if __name__ == "__main__":
    unittest.main()
