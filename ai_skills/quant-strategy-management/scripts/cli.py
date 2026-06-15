from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from quant_strategy_management.service import (  # noqa: E402
    get_quant_strategy_payload,
    list_quant_strategies_payload,
    parse_quant_strategy_payload,
    run_quant_backtest_payload,
    run_quant_strategy_from_prompt_payload,
    save_quant_strategy_payload,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Quant strategy management CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_parser = subparsers.add_parser("parse")
    parse_parser.add_argument("--prompt", required=True)

    save_parser = subparsers.add_parser("save")
    save_parser.add_argument("--prompt", default="")
    save_parser.add_argument("--dsl", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--limit", type=int, default=50)

    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("--strategy-id", type=int, required=True)

    backtest_parser = subparsers.add_parser("backtest")
    backtest_parser.add_argument("--strategy-id", type=int)
    backtest_parser.add_argument("--dsl")
    backtest_parser.add_argument("--prompt", default="")
    backtest_parser.add_argument("--source", default="akshare")

    run_parser = subparsers.add_parser("run-prompt")
    run_parser.add_argument("--prompt", required=True)
    run_parser.add_argument("--source", default="akshare")

    args = parser.parse_args()
    if args.command == "parse":
        payload = parse_quant_strategy_payload(args.prompt)
    elif args.command == "save":
        payload = save_quant_strategy_payload(args.prompt, args.dsl)
    elif args.command == "list":
        payload = list_quant_strategies_payload(args.limit)
    elif args.command == "get":
        payload = get_quant_strategy_payload(args.strategy_id)
    elif args.command == "backtest":
        payload = run_quant_backtest_payload(
            strategy_id=args.strategy_id,
            dsl=args.dsl,
            raw_prompt=args.prompt,
            source_preference=args.source,
        )
    else:
        payload = run_quant_strategy_from_prompt_payload(args.prompt, source_preference=args.source)
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
