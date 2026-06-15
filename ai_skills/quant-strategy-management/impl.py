import json
import os
import sys


SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
if SKILL_DIR not in sys.path:
    sys.path.insert(0, SKILL_DIR)

from quant_strategy_management.service import (
    get_quant_strategy_payload,
    list_quant_strategies_payload,
    parse_quant_strategy_payload,
    run_quant_backtest_payload,
    run_quant_strategy_from_prompt_payload,
    save_quant_strategy_payload,
)


def _json_response(payload):
    return json.dumps(payload, ensure_ascii=False)


def parse_quant_strategy(prompt):
    return _json_response(parse_quant_strategy_payload(prompt))


def save_quant_strategy(raw_prompt, dsl):
    return _json_response(save_quant_strategy_payload(raw_prompt, dsl))


def list_quant_strategies(limit=50):
    return _json_response(list_quant_strategies_payload(limit=limit))


def get_quant_strategy(strategy_id):
    return _json_response(get_quant_strategy_payload(strategy_id))


def run_quant_backtest(strategy_id=None, dsl=None, raw_prompt="", source_preference="akshare", persist=True):
    return _json_response(
        run_quant_backtest_payload(
            strategy_id=strategy_id,
            dsl=dsl,
            raw_prompt=raw_prompt,
            source_preference=source_preference,
            persist=bool(persist),
        )
    )


def run_quant_strategy_from_prompt(prompt, source_preference="akshare", persist=True):
    return _json_response(
        run_quant_strategy_from_prompt_payload(
            prompt,
            source_preference=source_preference,
            persist=bool(persist),
        )
    )


TOOL_EXPORTS = [
    {
        "name": "parse_quant_strategy",
        "handler": parse_quant_strategy,
        "description": "Parse a natural-language quant strategy idea into a controlled Strategy DSL JSON payload.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Natural-language strategy idea."}
            },
            "required": ["prompt"]
        },
        "allowed_modes": ["clarifying", "execution"],
        "read_only": True,
        "search_hint": "quant strategy dsl parse backtest planning"
    },
    {
        "name": "save_quant_strategy",
        "handler": save_quant_strategy,
        "description": "Save a quantitative strategy and its DSL snapshot into the standalone strategy asset store.",
        "parameters": {
            "type": "object",
            "properties": {
                "raw_prompt": {"type": "string", "description": "Original user prompt for traceability."},
                "dsl": {"type": "object", "description": "Strategy DSL object to persist."}
            },
            "required": ["raw_prompt", "dsl"]
        },
        "allowed_modes": ["execution"],
        "search_hint": "quant strategy save asset version"
    },
    {
        "name": "list_quant_strategies",
        "handler": list_quant_strategies,
        "description": "List saved quantitative strategy assets from the standalone store.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum number of strategies to return."}
            }
        },
        "allowed_modes": ["clarifying", "execution"],
        "read_only": True,
        "search_hint": "quant strategy list inventory backtests"
    },
    {
        "name": "get_quant_strategy",
        "handler": get_quant_strategy,
        "description": "Get a saved quantitative strategy, its latest DSL, and recorded backtests.",
        "parameters": {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "integer", "description": "Saved strategy identifier."}
            },
            "required": ["strategy_id"]
        },
        "allowed_modes": ["clarifying", "execution"],
        "read_only": True,
        "search_hint": "quant strategy details dsl report"
    },
    {
        "name": "run_quant_backtest",
        "handler": run_quant_backtest,
        "description": "Run a daily backtest for a saved strategy or an inline Strategy DSL and persist report artifacts.",
        "parameters": {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "integer", "description": "Existing saved strategy id."},
                "dsl": {"type": "object", "description": "Inline Strategy DSL when no strategy id is provided."},
                "raw_prompt": {"type": "string", "description": "Original strategy prompt when using an inline DSL."},
                "source_preference": {"type": "string", "description": "Preferred market data source, default akshare."},
                "persist": {"type": "boolean", "description": "Whether to save strategy/backtest records."}
            }
        },
        "allowed_modes": ["execution"],
        "search_hint": "quant strategy backtest daily report artifacts"
    },
    {
        "name": "run_quant_strategy_from_prompt",
        "handler": run_quant_strategy_from_prompt,
        "description": "One-shot parse, save, and backtest flow for a quantitative strategy idea.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Natural-language strategy idea."},
                "source_preference": {"type": "string", "description": "Preferred market data source, default akshare."},
                "persist": {"type": "boolean", "description": "Whether to save strategy and backtest records."}
            },
            "required": ["prompt"]
        },
        "allowed_modes": ["execution"],
        "search_hint": "quant strategy one shot parse save backtest"
    }
]
