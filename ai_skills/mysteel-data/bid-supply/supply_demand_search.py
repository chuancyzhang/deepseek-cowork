import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import fail_cli, print_json, request_json


def search_supply_demand(args):
    payload = {"type": args.type, "shopSpotLimit": args.shop_spot_limit}
    mapping = {
        "breedName": args.breed_name,
        "spec": args.spec,
        "material": args.material,
        "steelMill": args.steel_mill,
        "warehouseArea": args.warehouse_area,
        "warehouseName": args.warehouse_name,
    }
    payload.update({key: value for key, value in mapping.items() if value is not None})
    return request_json(
        "POST",
        "/mcp/info/api/external/gq/querySupplyDemandSpot",
        operation="supply_demand_search",
        json_body=payload,
    )


def build_parser():
    parser = argparse.ArgumentParser(description="搜索钢联钢材现货供需信息")
    parser.add_argument("--type", type=int, choices=(1, 2), required=True, help="1=供应信息，2=求购信息")
    parser.add_argument("--breed-name")
    parser.add_argument("--spec")
    parser.add_argument("--material")
    parser.add_argument("--steel-mill")
    parser.add_argument("--warehouse-area")
    parser.add_argument("--warehouse-name")
    parser.add_argument("--shop-spot-limit", type=int, default=5)
    return parser


def main():
    try:
        result = search_supply_demand(build_parser().parse_args())
        print_json({"success": True, "data": result.get("data", result)})
        return 0
    except Exception as exc:
        return fail_cli(exc)


if __name__ == "__main__":
    raise SystemExit(main())
