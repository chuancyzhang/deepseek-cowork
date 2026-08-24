import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import fail_cli, print_json, request_json


def analyze_market(query):
    payload = request_json(
        "POST",
        "/mcp/info/chat-robot/rag/answer",
        operation="market_analysis",
        json_body={"query": query},
        timeout_seconds=120,
    )
    analysis = payload.get("data") if isinstance(payload, dict) else None
    if not analysis:
        raise RuntimeError("钢联市场分析响应缺少 data。")
    return analysis


def build_parser():
    parser = argparse.ArgumentParser(description="生成钢联大宗商品市场分析")
    parser.add_argument("--query", required=True)
    return parser


def main():
    try:
        args = build_parser().parse_args()
        print_json({"success": True, "analysis": analyze_market(args.query)})
        return 0
    except Exception as exc:
        return fail_cli(exc)


if __name__ == "__main__":
    raise SystemExit(main())

