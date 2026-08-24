import argparse
import csv
import io
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import fail_cli, print_json, request_json, resolve_output_dir, unique_stem


def search_price(text):
    return request_json(
        "POST",
        "/mcp/info/ai-search/search",
        operation="price_search",
        json_body={
            "source": "MyClaw模式",
            "text": text,
            "indexSearchEnable": True,
            "infoSearchEnable": False,
            "staticKnowledgeEnable": True,
        },
    )


def _safe_filename(value):
    cleaned = re.sub(r'[:/\\*?"<>|]+', "_", str(value or "unknown")).strip(" ._")
    return cleaned[:80] or "unknown"


def _safe_csv_cell(value):
    text = str(value if value is not None else "")
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def _validate_date(value, label):
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} 必须为 YYYY-MM-DD 格式。") from exc


def save_csv_files(payload, *, output_dir, limit=0, start_date=None, end_date=None):
    index_data = ((payload.get("data") or {}).get("indexData") or []) if isinstance(payload, dict) else []
    saved = []
    for item in index_data:
        if not isinstance(item, dict):
            continue
        index_name = str(item.get("indexName") or item.get("indexShortName") or "unknown")
        unit = str(item.get("unitName") or "")
        data_map = item.get("dataMap") or {}
        if not isinstance(data_map, dict):
            continue
        dates = sorted(str(value) for value in data_map)
        dates = [value for value in dates if (not start_date or value >= start_date) and (not end_date or value <= end_date)]
        if limit > 0:
            dates = dates[-limit:]
        if not dates:
            continue

        rows = []
        previous = None
        for current_date in dates:
            raw_price = data_map[current_date]
            change = ""
            change_pct = ""
            try:
                price = float(raw_price)
                if previous is not None:
                    difference = price - previous
                    change = f"{difference:+.2f}" if difference else "0"
                    if previous:
                        change_pct = f"{difference / previous * 100:+.2f}%"
                previous = price
            except (TypeError, ValueError):
                previous = None
            rows.append((current_date, raw_price, unit, change, change_pct))

        buffer = io.StringIO(newline="")
        buffer.write(f"# index_name: {index_name}\n")
        buffer.write(f"# unit: {unit}\n")
        buffer.write(f"# total_rows: {len(rows)}\n")
        buffer.write(f"# date_range: {dates[0]} ~ {dates[-1]}\n#\n")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(("date", "price", "unit", "change", "change_pct"))
        for row in reversed(rows):
            writer.writerow(tuple(_safe_csv_cell(value) for value in row))

        output_file = output_dir / f"{_safe_filename(index_name)}-{unique_stem('price')}.csv"
        output_file.write_text(buffer.getvalue(), encoding="utf-8")
        saved.append({"file": str(output_file), "index_name": index_name, "unit": unit, "rows": len(rows)})
    return saved


def build_parser():
    parser = argparse.ArgumentParser(description="查询钢联价格和产业指标")
    parser.add_argument("query")
    parser.add_argument("--csv", action="store_true")
    parser.add_argument("--output-dir")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--days", type=int, default=0)
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    return parser


def main():
    try:
        args = build_parser().parse_args()
        if args.limit < 0 or args.days < 0:
            raise ValueError("limit 和 days 不得为负数。")
        start_date = _validate_date(args.start_date, "start-date")
        end_date = _validate_date(args.end_date, "end-date")
        if args.days and not start_date:
            start_date = (date.today() - timedelta(days=args.days)).isoformat()
        if start_date and end_date and start_date > end_date:
            raise ValueError("start-date 不得晚于 end-date。")
        result = search_price(args.query)
        if not args.csv:
            print_json({"success": True, "data": result})
            return 0
        files = save_csv_files(
            result,
            output_dir=resolve_output_dir("price-search", args.output_dir),
            limit=args.limit,
            start_date=start_date,
            end_date=end_date,
        )
        if not files:
            raise RuntimeError("钢联响应中没有可写入 CSV 的指标数据。")
        print_json({"success": True, "files": files})
        return 0
    except Exception as exc:
        return fail_cli(exc)


if __name__ == "__main__":
    raise SystemExit(main())
