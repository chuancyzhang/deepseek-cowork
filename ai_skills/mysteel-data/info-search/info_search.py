import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import fail_cli, print_json, request_json


def search_info(text):
    return request_json(
        "POST",
        "/mcp/info/ai-search/search",
        operation="info_search",
        json_body={
            "source": "MyClaw模式",
            "text": text,
            "indexSearchEnable": False,
            "infoSearchEnable": True,
            "staticKnowledgeEnable": True,
        },
    )


def build_parser():
    parser = argparse.ArgumentParser(description="搜索钢联大宗商品资讯")
    parser.add_argument("query")
    return parser


def main():
    try:
        args = build_parser().parse_args()
        print_json({"success": True, "data": search_info(args.query)})
        return 0
    except Exception as exc:
        return fail_cli(exc)


if __name__ == "__main__":
    raise SystemExit(main())

