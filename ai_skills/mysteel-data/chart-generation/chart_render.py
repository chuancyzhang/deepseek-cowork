import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import fail_cli, print_json, resolve_output_dir, resolve_workspace_path, unique_stem


def _safe_script_json(payload):
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_chart(args):
    option_file = resolve_workspace_path(args.option_file, default_relative="mysteel/output/chart-generation")
    if not option_file.is_file():
        raise FileNotFoundError(f"图表配置文件不存在：{option_file}")
    option = json.loads(option_file.read_text(encoding="utf-8"))

    title = args.title
    if not title:
        meta_file = option_file.with_name(option_file.name.replace("_option.json", "_meta.json"))
        if meta_file.is_file():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            title = str(meta.get("title") or "")
    title = title or "钢联 AI 图表"

    output_dir = resolve_output_dir("chart-generation", args.output_dir)
    output_file = output_dir / f"{unique_stem('mysteel-chart')}.html"
    escaped_title = html.escape(title, quote=True)
    option_json = _safe_script_json(option)
    document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escaped_title}</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1f2329; background: #f7f8fa; }}
    main {{ max-width: 1200px; margin: 24px auto; padding: 24px; background: #fff; }}
    h1 {{ margin: 0 0 20px; font-size: 22px; font-weight: 600; }}
    #chart {{ width: 100%; height: 620px; }}
    p {{ color: #646a73; font-size: 12px; }}
  </style>
</head>
<body>
  <main>
    <h1>{escaped_title}</h1>
    <div id="chart" aria-label="{escaped_title}"></div>
    <p>数据与图表配置来源：钢联数据（Mysteel）</p>
  </main>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
  <script>
    const chart = echarts.init(document.getElementById("chart"));
    const option = {option_json};
    chart.setOption(option);
    window.addEventListener("resize", () => chart.resize());
  </script>
</body>
</html>
"""
    output_file.write_text(document, encoding="utf-8")
    return output_file


def build_parser():
    parser = argparse.ArgumentParser(description="将钢联 ECharts 配置渲染为工作区 HTML")
    parser.add_argument("--option-file", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--title")
    return parser


def main():
    try:
        output_file = render_chart(build_parser().parse_args())
        print_json({"success": True, "html_file": str(output_file)})
        return 0
    except Exception as exc:
        return fail_cli(exc)


if __name__ == "__main__":
    raise SystemExit(main())

