import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import fail_cli, print_json, request_json


def query_weather(breed):
    return request_json(
        "GET",
        "/mcp/weather/getWeather",
        operation="weather_query",
        params={"breed": breed.upper()},
    )


def build_parser():
    parser = argparse.ArgumentParser(description="查询钢联农产品主产区天气")
    parser.add_argument("--breed", required=True, help="大写期货品种代码，例如 C、A、CF")
    return parser


def main():
    try:
        args = build_parser().parse_args()
        print_json({"success": True, "breed": args.breed.upper(), "data": query_weather(args.breed)})
        return 0
    except Exception as exc:
        return fail_cli(exc)


if __name__ == "__main__":
    raise SystemExit(main())

