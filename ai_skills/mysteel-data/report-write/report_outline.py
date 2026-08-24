import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import fail_cli, print_json, request_json


def generate_outline(query):
    payload = request_json(
        "POST",
        "/mcp/info/chat-robot/rag/answer",
        operation="report_outline",
        json_body={"query": query},
        timeout_seconds=120,
    )
    outline = payload.get("data") if isinstance(payload, dict) else None
    if not outline:
        raise RuntimeError("钢联研报响应缺少 data。")
    return outline


def build_parser():
    parser = argparse.ArgumentParser(description="生成钢联大宗商品研报梗概")
    parser.add_argument("--query", required=True)
    return parser


def main():
    try:
        args = build_parser().parse_args()
        print_json({"success": True, "outline": generate_outline(args.query)})
        return 0
    except Exception as exc:
        return fail_cli(exc)


if __name__ == "__main__":
    raise SystemExit(main())
