import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import fail_cli, print_json, request_json


def earliest_timestamp_ms(now=None):
    current = now or datetime.now(timezone.utc)
    return int(datetime(current.year - 2, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def search_bidding(args):
    start_time = args.start_time if args.start_time is not None else earliest_timestamp_ms()
    end_time = args.end_time if args.end_time is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    if start_time < earliest_timestamp_ms():
        raise ValueError("招投标查询仅支持当前自然年及前两个自然年的数据。")
    if end_time < start_time:
        raise ValueError("end-time 不得早于 start-time。")
    payload = {
        "query": args.query,
        "startTime": start_time,
        "endTime": end_time,
        "topK": args.top_k,
        "innerType": 18,
        "onlyOriginData": True,
    }
    return request_json("POST", "/mcp/info/vector/rag-search", operation="bidding_search", json_body=payload)


def build_parser():
    parser = argparse.ArgumentParser(description="搜索钢联招投标数据")
    parser.add_argument("--query", required=True)
    parser.add_argument("--start-time", type=int)
    parser.add_argument("--end-time", type=int)
    parser.add_argument("--top-k", type=int, default=10)
    return parser


def main():
    try:
        result = search_bidding(build_parser().parse_args())
        print_json({"success": True, "data": result.get("data", result)})
        return 0
    except Exception as exc:
        return fail_cli(exc)


if __name__ == "__main__":
    raise SystemExit(main())

