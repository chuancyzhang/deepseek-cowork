import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SKILL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai_skills", "quant-strategy-management")
if SKILL_DIR not in sys.path:
    sys.path.insert(0, SKILL_DIR)

from core.skill_manager import SkillManager
from quant_strategy_management.service import (
    get_quant_strategy_payload,
    list_quant_strategies_payload,
    parse_quant_strategy_payload,
    run_quant_backtest_payload,
    save_quant_strategy_payload,
)


def sample_bars() -> pd.DataFrame:
    rows = []
    closes = [10, 10.2, 10.1, 10.4, 10.8, 11.0, 10.9, 11.3, 11.5, 11.4, 11.8, 12.0, 11.9, 12.2, 12.4]
    for index, close in enumerate(closes, start=1):
        rows.append(
            {
                "datetime": pd.Timestamp(f"2024-01-{index:02d}"),
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1000 + index,
                "amount": close * (1000 + index),
                "open_interest": 0,
            }
        )
    return pd.DataFrame(rows).set_index("datetime")


class QuantStrategyServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        os.environ["QSM_DATA_ROOT"] = self.temp_dir

    def tearDown(self):
        os.environ.pop("QSM_DATA_ROOT", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_parse_strategy_payload_returns_valid_dsl(self):
        payload = parse_quant_strategy_payload("用5日和20日均线做沪深300趋势策略，2024年回测，100万资金")

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["dsl"]["strategy_name"], "双均线趋势策略")
        self.assertEqual(payload["dsl"]["symbols"], ["510300.SH"])

    def test_save_list_and_get_strategy_payloads(self):
        parsed = parse_quant_strategy_payload("做一个RSI策略，观察上证50，2024年")
        saved = save_quant_strategy_payload("做一个RSI策略，观察上证50，2024年", parsed["dsl"])
        listed = list_quant_strategies_payload()
        loaded = get_quant_strategy_payload(saved["strategy_id"])

        self.assertTrue(saved["ok"])
        self.assertEqual(len(listed["strategies"]), 1)
        self.assertEqual(loaded["strategy"]["id"], saved["strategy_id"])
        self.assertEqual(loaded["strategy"]["dsl"]["strategy_name"], "RSI动量策略")

    def test_run_quant_backtest_persists_artifacts(self):
        parsed = parse_quant_strategy_payload("用5日和20日均线做沪深300趋势策略，2024年回测，100万资金")

        def loader(_symbol, _dsl, _source):
            return sample_bars()

        result = run_quant_backtest_payload(
            dsl=parsed["dsl"],
            raw_prompt="用5日和20日均线做沪深300趋势策略，2024年回测，100万资金",
            market_data_loader=loader,
        )

        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["strategy_id"])
        self.assertIsNotNone(result["backtest_id"])
        self.assertIn("summary", result)
        self.assertTrue(os.path.exists(result["artifact_paths"]["equity_curve"]))
        self.assertTrue(os.path.exists(result["artifact_paths"]["report"]))


class QuantStrategySkillIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.skills_dir = os.path.join(self.temp_dir, "skills")
        self.ai_skills_dir = os.path.join(self.temp_dir, "ai_skills")
        os.makedirs(self.skills_dir, exist_ok=True)
        os.makedirs(self.ai_skills_dir, exist_ok=True)
        shutil.copytree(SKILL_DIR, os.path.join(self.ai_skills_dir, "quant-strategy-management"))
        os.environ["QSM_DATA_ROOT"] = os.path.join(self.temp_dir, "quant-data")

    def tearDown(self):
        os.environ.pop("QSM_DATA_ROOT", None)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _build_manager(self):
        sm = SkillManager(workspace_dir=self.temp_dir)
        sm.skills_dirs = [self.skills_dir, self.ai_skills_dir]
        with patch("core.skill_manager.install_skill_dependencies", return_value={"ok": True, "message": "mocked"}):
            sm.load_skills()
        return sm

    def test_skill_manager_loads_and_discovers_quant_skill(self):
        sm = self._build_manager()
        result = sm.call_tool(
            "tool_search",
            {"query": "量化策略 回测 DSL"},
            context={
                "run_context": {"mode": "execution"},
                "discovered_tool_names": set(),
            },
        )

        self.assertEqual(result["status"], "ok")
        self.assertIn("parse_quant_strategy", result["discovered_tools"])
        self.assertIn("run_quant_backtest", result["discovered_tools"])
        self.assertTrue(any(skill["name"] == "quant-strategy-management" for skill in result["skills"]))

    def test_tool_outputs_are_structured_json(self):
        sm = self._build_manager()
        parsed = json.loads(sm.call_tool("parse_quant_strategy", {"prompt": "做一个RSI策略，观察上证50，2024年"}, context={}))
        self.assertTrue(parsed["ok"])

        with patch("quant_strategy_management.data.MarketDataProvider.load_bars", return_value={"510050.SH": sample_bars()}):
            backtest = json.loads(
                sm.call_tool(
                    "run_quant_backtest",
                    {
                        "dsl": parsed["dsl"],
                        "raw_prompt": "做一个RSI策略，观察上证50，2024年",
                    },
                    context={},
                )
            )
        self.assertTrue(backtest["ok"])
        self.assertIn("backtest_id", backtest)
        self.assertIn("artifact_paths", backtest)
        self.assertIn("summary", backtest)
