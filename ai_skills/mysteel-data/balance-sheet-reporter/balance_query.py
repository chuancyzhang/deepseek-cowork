import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import fail_cli, print_json, request_json


def query_balance(args):
    if not args.crop_year.endswith("年度"):
        raise ValueError("crop-year 必须为 YYYY年度 格式，例如 2025年度。")
    return request_json(
        "POST",
        "/mcp/usda/queryData",
        operation="balance_query",
        json_body={
            "breedClass": args.breed_class,
            "breedName": args.breed_name,
            "area": args.area,
            "cropYear": args.crop_year,
        },
    )


def build_parser():
    parser = argparse.ArgumentParser(description="查询钢联农产品供需平衡数据")
    parser.add_argument("--breed-class", required=True)
    parser.add_argument("--breed-name", required=True)
    parser.add_argument("--area", required=True)
    parser.add_argument("--crop-year", required=True)
    return parser


def main():
    try:
        print_json({"success": True, "data": query_balance(build_parser().parse_args())})
        return 0
    except Exception as exc:
        return fail_cli(exc)


if __name__ == "__main__":
    raise SystemExit(main())

