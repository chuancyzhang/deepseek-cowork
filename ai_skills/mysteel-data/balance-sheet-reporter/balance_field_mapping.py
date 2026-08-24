import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import fail_cli, print_json, request_json


def query_field_mapping(breed_name):
    return request_json(
        "GET",
        "/mcp/usda/fieldMapping",
        operation="balance_field_mapping",
        params={"breed": breed_name},
    )


def build_parser():
    parser = argparse.ArgumentParser(description="查询钢联农产品供需字段和单位")
    parser.add_argument("--breed-name", required=True)
    return parser


def main():
    try:
        args = build_parser().parse_args()
        print_json({"success": True, "data": query_field_mapping(args.breed_name)})
        return 0
    except Exception as exc:
        return fail_cli(exc)


if __name__ == "__main__":
    raise SystemExit(main())

