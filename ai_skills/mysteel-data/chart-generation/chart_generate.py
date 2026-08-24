import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import fail_cli, print_json, request_json, resolve_output_dir, unique_stem


def _parse_json(value, label):
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} 必须是有效 JSON：{exc}") from exc


def generate_chart(args):
    request_id = "req-" + uuid.uuid4().hex[:12]
    payload = {
        "requestId": request_id,
        "task": args.task,
        "mode": args.mode,
        "asyncEnable": False,
    }
    optional = {
        "data": _parse_json(args.data, "data"),
        "type": args.type,
        "dataExample": _parse_json(args.data_example, "data-example"),
        "dataDescription": args.data_description,
        "option": _parse_json(args.option, "option"),
        "sessionId": args.session_id,
    }
    payload.update({key: value for key, value in optional.items() if value is not None})
    if args.robust_mode:
        payload["robustMode"] = True

    response = request_json(
        "POST",
        "/mcp/info/genie-tool/v1/tool/ai-chart",
        operation="chart_generate",
        json_body=payload,
        timeout_seconds=120,
    )
    result = response.get("data") if isinstance(response, dict) else None
    if not isinstance(result, dict) or result.get("option") is None:
        raise RuntimeError("钢联图表响应缺少 data.option。")

    output_dir = resolve_output_dir("chart-generation", args.output_dir)
    response_id = str(result.get("requestId") or request_id)
    stem = unique_stem(response_id)
    option_file = output_dir / f"{stem}_option.json"
    meta_file = output_dir / f"{stem}_meta.json"
    option_file.write_text(json.dumps(result["option"], ensure_ascii=False, indent=2), encoding="utf-8")
    meta_file.write_text(
        json.dumps(
            {"requestId": response_id, "task": args.task, "title": args.title or args.task.strip()[:80]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "request_id": response_id,
        "option_file": str(option_file),
        "meta_file": str(meta_file),
        "option_url": result.get("optionUrl"),
        "preview_url": result.get("previewUrl"),
    }


def build_parser():
    parser = argparse.ArgumentParser(description="调用钢联 AI 图表接口生成 ECharts 配置")
    parser.add_argument("--task", required=True)
    parser.add_argument("--mode", default="FREEDOM", choices=("FREEDOM", "STRICT", "TEMPLATE", "AUTO"))
    parser.add_argument("--data")
    parser.add_argument("--type")
    parser.add_argument("--data-example")
    parser.add_argument("--data-description")
    parser.add_argument("--option")
    parser.add_argument("--session-id")
    parser.add_argument("--robust-mode", action="store_true")
    parser.add_argument("--title")
    parser.add_argument("--output-dir")
    return parser


def main():
    try:
        result = generate_chart(build_parser().parse_args())
        print_json({"success": True, **result})
        return 0
    except Exception as exc:
        return fail_cli(exc)


if __name__ == "__main__":
    raise SystemExit(main())

