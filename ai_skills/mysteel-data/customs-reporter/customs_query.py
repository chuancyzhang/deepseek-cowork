import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import fail_cli, print_json, request_json


def query_customs(args):
    payload = {
        "productName": args.product_name or "",
        "hsCode": args.hs_code or "",
        "startDate": args.start_date,
        "endDate": args.end_date,
        "tradeType": args.trade_type,
        "ccType": args.cc_type,
        "country": args.country or "",
        "dataType": args.data_type,
    }
    return request_json("POST", "/mcp/custom/queryData", operation="customs_query", json_body=payload)


def build_parser():
    parser = argparse.ArgumentParser(description="查询钢联海关进出口数据")
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--product-name")
    identity.add_argument("--hs-code")
    parser.add_argument("--start-date", required=True, help="yyyy-MM")
    parser.add_argument("--end-date", required=True, help="yyyy-MM")
    parser.add_argument("--trade-type", required=True, choices=("import", "export"))
    parser.add_argument("--cc-type", default="usd", choices=("cny", "usd"))
    parser.add_argument("--country")
    parser.add_argument("--data-type", default="monthly", choices=("monthly", "summary"))
    return parser


def main():
    try:
        result = query_customs(build_parser().parse_args())
        print_json({"success": True, "data": result.get("data", result)})
        return 0
    except Exception as exc:
        return fail_cli(exc)


if __name__ == "__main__":
    raise SystemExit(main())

