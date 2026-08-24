import argparse
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from core.skill_manager import SkillManager


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_ROOT = os.path.join(REPO_ROOT, "ai_skills", "mysteel-data")


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

    def is_skill_enabled(self, skill_name, default_enabled=True):
        if self.enable_target and skill_name == "mysteel-data":
            return True
        return default_enabled

    def get_mcp_servers(self):
        return []

    def get(self, _key, default=None):
        return default

    def get_skill_config(self, skill_name):
        return dict(self.values.get(skill_name, {}))


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class TestMysteelDataSkill(unittest.TestCase):
    def setUp(self):
        self.workspace_dir = tempfile.mkdtemp()
        self.previous_runtime = sys.modules.get("runtime_support")
        self.runtime = load_module("runtime_support", "runtime_support.py")
        sys.modules["runtime_support"] = self.runtime

    def tearDown(self):
        shutil.rmtree(self.workspace_dir, ignore_errors=True)
        if self.previous_runtime is None:
            sys.modules.pop("runtime_support", None)
        else:
            sys.modules["runtime_support"] = self.previous_runtime

    def build_manager(self, values=None, enable_target=True):
        manager = SkillManager(
            workspace_dir=self.workspace_dir,
            config_manager=ConfigStub(values, enable_target=enable_target),
            auto_load=False,
            load_mcp_tools=False,
        )
        manager.skills_dirs = [os.path.join(REPO_ROOT, "ai_skills")]
        manager.load_skills(load_mcp_tools=False)
        return manager

    def test_manifest_registers_default_off_bundle_and_twelve_python_entries(self):
        manager = self.build_manager()
        record = manager.skill_records["mysteel-data"]
        spec = record["spec"]
        expected = {
            "customs_query", "balance_query", "balance_field_mapping", "weather_query",
            "bidding_search", "supply_demand_search", "info_search", "market_analysis",
            "report_outline", "chart_generate", "chart_render", "price_search",
        }
        self.assertEqual(spec["source_type"], "bundled_plugin")
        self.assertEqual(spec["source_format"], "agent_skill")
        self.assertFalse(spec["default_enabled"])
        self.assertEqual({entry["name"] for entry in spec["script_entries"]}, expected)
        self.assertTrue(all(entry["runtime"] == "python" for entry in spec["script_entries"]))
        self.assertEqual(spec["node_dependencies"], [])
        self.assertEqual(spec["python_dependencies"], ["requests>=2.31,<3"])
        self.assertTrue(manager.validate_skill("mysteel-data")["ok"])

        catalog_manager = self.build_manager(enable_target=False)
        catalog_entry = next(item for item in catalog_manager.get_all_skills() if item["name"] == "mysteel-data")
        self.assertFalse(catalog_entry["enabled"])
        self.assertEqual(catalog_entry["type"], "bundled_plugin")

    def test_one_secret_config_and_acquisition_link(self):
        manager = self.build_manager({"mysteel-data": {"MYSTEEL_API_KEY": "secret-test-key"}})
        fields = manager.get_skill_config_fields("mysteel-data")
        self.assertEqual(len(fields), 1)
        self.assertEqual(fields[0]["kind"], "secret")
        self.assertEqual(fields[0]["action_url"], "https://data.mysteel.com/skills/index.html")
        self.assertEqual(manager.build_skill_config_env("mysteel-data"), {"MYSTEEL_API_KEY": "secret-test-key"})
        with self.assertRaisesRegex(ValueError, "MYSTEEL_API_KEY"):
            self.build_manager().build_skill_config_env("mysteel-data")

    def test_runtime_retries_once_and_never_logs_secret(self):
        stderr = io.StringIO()
        with patch.dict(
            os.environ,
            {"MYSTEEL_API_KEY": "never-log-this-key", "COWORK_WORKSPACE_DIR": self.workspace_dir},
            clear=True,
        ), patch.object(
            self.runtime.requests,
            "request",
            side_effect=[requests.Timeout("timeout with no secret"), Response({"code": "200", "data": {"ok": True}})],
        ) as request_mock, redirect_stderr(stderr):
            result = self.runtime.request_json("GET", "/example", operation="test")

        self.assertEqual(result["data"], {"ok": True})
        self.assertEqual(request_mock.call_count, 2)
        self.assertIn('"status": "retry"', stderr.getvalue())
        self.assertIn('"status": "finish"', stderr.getvalue())
        self.assertNotIn("never-log-this-key", stderr.getvalue())

    def test_auth_error_is_explicit_and_not_retried(self):
        stderr = io.StringIO()
        with patch.dict(
            os.environ,
            {"MYSTEEL_API_KEY": "never-log-this-key", "COWORK_WORKSPACE_DIR": self.workspace_dir},
            clear=True,
        ), patch.object(
            self.runtime.requests,
            "request",
            return_value=Response({"message": "unauthorized"}, status_code=401),
        ) as request_mock, redirect_stderr(stderr), self.assertRaisesRegex(self.runtime.MysteelAPIError, "无效"):
            self.runtime.request_json("GET", "/example", operation="test")

        self.assertEqual(request_mock.call_count, 1)
        self.assertNotIn("never-log-this-key", stderr.getvalue())

    def test_server_error_retries_once_then_surfaces_failure(self):
        with patch.dict(
            os.environ,
            {"MYSTEEL_API_KEY": "secret", "COWORK_WORKSPACE_DIR": self.workspace_dir},
            clear=True,
        ), patch.object(
            self.runtime.requests,
            "request",
            side_effect=[Response({"message": "temporary"}, 503), Response({"message": "still down"}, 503)],
        ) as request_mock, self.assertRaisesRegex(self.runtime.MysteelAPIError, "HTTP 503"):
            self.runtime.request_json("GET", "/example", operation="test")
        self.assertEqual(request_mock.call_count, 2)

    def test_all_remote_entrypoints_keep_upstream_endpoint_and_payload_semantics(self):
        cases = []

        customs = load_module("mysteel_customs_test", "customs-reporter/customs_query.py")
        args = customs.build_parser().parse_args(["--product-name", "钢材", "--start-date", "2025-01", "--end-date", "2025-12", "--trade-type", "export"])
        cases.append((customs, lambda parsed=args: customs.query_customs(parsed), "/mcp/custom/queryData", {"productName": "钢材", "tradeType": "export"}))

        balance = load_module("mysteel_balance_test", "balance-sheet-reporter/balance_query.py")
        args = balance.build_parser().parse_args(["--breed-class", "谷物", "--breed-name", "玉米", "--area", "中国", "--crop-year", "2025年度"])
        cases.append((balance, lambda parsed=args: balance.query_balance(parsed), "/mcp/usda/queryData", {"breedName": "玉米"}))

        fields = load_module("mysteel_fields_test", "balance-sheet-reporter/balance_field_mapping.py")
        cases.append((fields, lambda: fields.query_field_mapping("玉米"), "/mcp/usda/fieldMapping", {"breed": "玉米"}))

        weather = load_module("mysteel_weather_test", "weather-reporter/weather_query.py")
        cases.append((weather, lambda: weather.query_weather("cf"), "/mcp/weather/getWeather", {"breed": "CF"}))

        bidding = load_module("mysteel_bidding_test", "bid-supply/bidding_search.py")
        args = SimpleNamespace(query="钢材招标", start_time=None, end_time=None, top_k=8)
        cases.append((bidding, lambda parsed=args: bidding.search_bidding(parsed), "/mcp/info/vector/rag-search", {"innerType": 18, "topK": 8}))

        supply = load_module("mysteel_supply_test", "bid-supply/supply_demand_search.py")
        args = SimpleNamespace(type=1, shop_spot_limit=5, breed_name="螺纹钢", spec=None, material=None, steel_mill=None, warehouse_area="上海", warehouse_name=None)
        cases.append((supply, lambda parsed=args: supply.search_supply_demand(parsed), "/mcp/info/api/external/gq/querySupplyDemandSpot", {"type": 1, "breedName": "螺纹钢"}))

        info = load_module("mysteel_info_test", "info-search/info_search.py")
        cases.append((info, lambda: info.search_info("钢铁资讯"), "/mcp/info/ai-search/search", {"infoSearchEnable": True, "indexSearchEnable": False}))

        price = load_module("mysteel_price_remote_test", "price-search/price_search.py")
        cases.append((price, lambda: price.search_price("螺纹钢价格"), "/mcp/info/ai-search/search", {"indexSearchEnable": True, "infoSearchEnable": False}))

        for module, invoke, endpoint, expected in cases:
            with self.subTest(endpoint=endpoint, module=module.__name__), patch.object(module, "request_json", return_value={"code": "200"}) as request_mock:
                invoke()
                self.assertEqual(request_mock.call_args.args[1], endpoint)
                supplied = request_mock.call_args.kwargs.get("json_body") or request_mock.call_args.kwargs.get("params")
                for key, value in expected.items():
                    self.assertEqual(supplied[key], value)

        for relative_path, function_name, result_key in (
            ("market-analysis/market_analysis.py", "analyze_market", "analysis"),
            ("report-write/report_outline.py", "generate_outline", "outline"),
        ):
            module = load_module("mysteel_" + result_key + "_test", relative_path)
            with patch.object(module, "request_json", return_value={"code": "200", "data": "result"}) as request_mock:
                self.assertEqual(getattr(module, function_name)("query"), "result")
                self.assertEqual(request_mock.call_args.args[1], "/mcp/info/chat-robot/rag/answer")

    def test_price_csv_is_filtered_unique_and_never_deletes_existing_files(self):
        price = load_module("mysteel_price_files_test", "price-search/price_search.py")
        output_dir = Path(self.workspace_dir, "mysteel", "output", "price-search")
        output_dir.mkdir(parents=True)
        existing = output_dir / "existing.csv"
        existing.write_text("keep", encoding="utf-8")
        payload = {
            "data": {
                "indexData": [{
                    "indexName": "螺纹钢/上海",
                    "unitName": "元/吨",
                    "dataMap": {"2025-01-01": "3500", "2025-01-02": "3510", "2025-01-03": "3490"},
                }]
            }
        }
        first = price.save_csv_files(payload, output_dir=output_dir, limit=2)
        second = price.save_csv_files(payload, output_dir=output_dir, limit=2)

        self.assertTrue(existing.exists())
        self.assertNotEqual(first[0]["file"], second[0]["file"])
        content = Path(first[0]["file"]).read_text(encoding="utf-8")
        self.assertIn("# total_rows: 2", content)
        self.assertIn("2025-01-03", content)
        self.assertNotIn("2025-01-01", content)

    def test_chart_render_escapes_markup_and_rejects_workspace_escape(self):
        chart = load_module("mysteel_chart_render_test", "chart-generation/chart_render.py")
        option_file = Path(self.workspace_dir, "option.json")
        option_file.write_text(json.dumps({"title": {"text": "</script><script>alert(1)</script>"}}), encoding="utf-8")
        args = argparse.Namespace(option_file=str(option_file), output_dir=None, title='<img src=x onerror="alert(1)">')
        with patch.dict(os.environ, {"COWORK_WORKSPACE_DIR": self.workspace_dir}, clear=True):
            output_file = chart.render_chart(args)
            document = output_file.read_text(encoding="utf-8")
            self.assertIn("&lt;img", document)
            self.assertNotIn("</script><script>alert(1)</script>", document)
            self.assertIn("\\u003c/script\\u003e", document)
            outside = Path(self.workspace_dir).parent / "outside.json"
            with self.assertRaisesRegex(RuntimeError, "工作区内"):
                self.runtime.resolve_workspace_path(outside, default_relative="unused")

    def test_chart_generation_writes_unique_workspace_files(self):
        chart = load_module("mysteel_chart_generate_test", "chart-generation/chart_generate.py")
        args = argparse.Namespace(
            task="生成钢材价格折线图",
            mode="TEMPLATE",
            data='[{"name":"A","value":1}]',
            type="折线图",
            data_example=None,
            data_description=None,
            option=None,
            session_id=None,
            robust_mode=False,
            title="钢材价格",
            output_dir=None,
        )
        response = {"code": "200", "data": {"requestId": "server-request", "option": {"series": [{"data": [1]}]}}}
        existing = Path(self.workspace_dir, "existing.txt")
        existing.write_text("keep", encoding="utf-8")
        with patch.dict(
            os.environ,
            {"MYSTEEL_API_KEY": "secret", "COWORK_WORKSPACE_DIR": self.workspace_dir},
            clear=True,
        ), patch.object(chart, "request_json", return_value=response) as request_mock:
            first = chart.generate_chart(args)
            second = chart.generate_chart(args)

        self.assertEqual(request_mock.call_args.args[1], "/mcp/info/genie-tool/v1/tool/ai-chart")
        self.assertEqual(request_mock.call_args.kwargs["json_body"]["asyncEnable"], False)
        self.assertNotEqual(first["option_file"], second["option_file"])
        self.assertTrue(Path(first["option_file"]).is_file())
        self.assertTrue(Path(first["meta_file"]).is_file())
        self.assertTrue(existing.exists())
        Path(first["option_file"]).resolve().relative_to(Path(self.workspace_dir).resolve())

    def test_static_adaptations_and_source_provenance(self):
        python_text = "\n".join(path.read_text(encoding="utf-8") for path in Path(SKILL_ROOT).rglob("*.py"))
        for forbidden in ("api_key.md", "--api_key", "subprocess.run", "curl -", "openclaw-output"):
            self.assertNotIn(forbidden, python_text)
        self.assertIn("COWORK_WORKSPACE_DIR", python_text)
        self.assertIn("MYSTEEL_API_KEY", python_text)

        source = Path(SKILL_ROOT, "SOURCE.md").read_text(encoding="utf-8")
        for archive in ("海关数据查询.zip", "农产品供需分析.zip", "气象数据查询.zip", "商机探查.zip", "实时资讯.zip", "市场分析.zip", "研报撰写.zip", "智能图表生成.zip", "智能问数.zip"):
            self.assertIn(archive, source)
        self.assertIn("None of the archives included a license", source)


if __name__ == "__main__":
    unittest.main()
