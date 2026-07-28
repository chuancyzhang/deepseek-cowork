import argparse
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_support import emit


PUBLIC_URL = "https://finviz.com/groups.ashx?g=industry&v=210&o=-perf1w"
ELITE_URL = "https://elite.finviz.com/grp_export.ashx?g=industry&v=210&auth={api_key}"
FIELD_ALIASES = {
    "name": ("name", "industry"),
    "week": ("perf week", "perf_week", "week", "perf1w"),
    "month": ("perf month", "perf_month", "month", "perf1m"),
    "quarter": ("perf quart", "perf quarter", "perf_quart", "quarter", "perf3m"),
}


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, _attrs):
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def fail(code, message):
    emit("error", "theme_detect", error_code=code)
    print(json.dumps({"ok": False, "error": {"code": code, "message": message}}, ensure_ascii=False))
    raise SystemExit(1)


def parse_percent(value):
    text = str(value or "").strip().replace("%", "").replace(",", "")
    if text in {"", "-", "N/A", "n/a"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_headers(row):
    return [re.sub(r"\s+", " ", str(value).strip().lower()) for value in row]


def find_column(headers, field):
    aliases = FIELD_ALIASES[field]
    for index, header in enumerate(headers):
        if header in aliases:
            return index
    return None


def rows_to_industries(rows):
    header_index = next(
        (index for index, row in enumerate(rows) if find_column(normalize_headers(row), "name") is not None),
        None,
    )
    if header_index is None:
        fail("PARSE_ERROR", "FINVIZ response did not contain an industry table.")
    headers = normalize_headers(rows[header_index])
    indexes = {field: find_column(headers, field) for field in FIELD_ALIASES}
    if indexes["name"] is None or indexes["week"] is None:
        fail("PARSE_ERROR", "FINVIZ response is missing Name or Perf Week columns.")
    industries = []
    for row in rows[header_index + 1 :]:
        if len(row) <= max(index for index in indexes.values() if index is not None):
            continue
        name = row[indexes["name"]].strip()
        week = parse_percent(row[indexes["week"]])
        if not name or week is None:
            continue
        month = parse_percent(row[indexes["month"]]) if indexes["month"] is not None else None
        quarter = parse_percent(row[indexes["quarter"]]) if indexes["quarter"] is not None else None
        components = [(week, 0.5), (month, 0.3), (quarter, 0.2)]
        available = [(value, weight) for value, weight in components if value is not None]
        score = sum(value * weight for value, weight in available) / sum(weight for _, weight in available)
        industries.append(
            {
                "industry": name,
                "perf_week_pct": week,
                "perf_month_pct": month,
                "perf_quarter_pct": quarter,
                "momentum_score": round(score, 4),
            }
        )
    if not industries:
        fail("NO_RESULTS", "FINVIZ returned no usable industry rows.")
    return industries


def parse_payload(text, mode):
    if mode == "elite":
        return rows_to_industries(list(csv.reader(io.StringIO(text))))
    parser = TableParser()
    parser.feed(text)
    return rows_to_industries(parser.rows)


def fetch_payload(mode, api_key, timeout):
    url = ELITE_URL.format(api_key=api_key) if mode == "elite" else PUBLIC_URL
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Cowork-Wind-AIFinMarket/1.0", "Accept": "text/csv,text/html"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8-sig", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            fail("AUTH_ERROR", f"FINVIZ authentication failed with HTTP {exc.code}.")
        if exc.code == 429:
            fail("QUOTA_ERROR", "FINVIZ request quota or rate limit was exceeded.")
        fail("NETWORK_ERROR", f"FINVIZ returned HTTP {exc.code}.")
    except TimeoutError:
        fail("TIMEOUT", f"FINVIZ request timed out after {timeout} seconds.")
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            fail("TIMEOUT", f"FINVIZ request timed out after {timeout} seconds.")
        fail("NETWORK_ERROR", f"FINVIZ request failed: {type(reason).__name__}.")


def workspace_output(relative):
    workspace = str(os.environ.get("COWORK_WORKSPACE_DIR") or "").strip()
    if not workspace:
        fail("WORKSPACE_REQUIRED", "COWORK_WORKSPACE_DIR is required.")
    root = Path(workspace).resolve()
    if not root.is_dir():
        fail("WORKSPACE_REQUIRED", f"COWORK_WORKSPACE_DIR does not exist: {root}")
    target = (root / relative).resolve()
    if os.path.commonpath([str(root), str(target)]) != str(root):
        fail("INVALID_OUTPUT_PATH", "Output path escapes COWORK_WORKSPACE_DIR.")
    return target


def main():
    parser = argparse.ArgumentParser(description="Detect leading and lagging industries from one explicit FINVIZ source.")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", default="wind-aifinmarket/theme-detect.json")
    parser.add_argument("--fixture", help=argparse.SUPPRESS)
    args = parser.parse_args()
    emit("submit", "theme_detect")
    mode = str(os.environ.get("FINVIZ_MODE") or "public").strip().lower()
    if mode not in {"public", "elite"}:
        fail("CONFIG_ERROR", "FINVIZ_MODE must be 'public' or 'elite'.")
    api_key = str(os.environ.get("FINVIZ_API_KEY") or "").strip()
    if mode == "elite" and not api_key:
        fail("AUTH_ERROR", "FINVIZ_API_KEY is required when FINVIZ_MODE=elite.")
    top = max(1, min(args.top, 50))
    emit("start", "theme_detect", source=f"FINVIZ-{mode}")
    started = time.time()
    emit("run", "theme_detect", source=f"FINVIZ-{mode}")
    if args.fixture:
        fixture = Path(args.fixture).resolve()
        if not fixture.is_file():
            fail("FIXTURE_ERROR", f"Fixture does not exist: {fixture}")
        payload = fixture.read_text(encoding="utf-8-sig")
    else:
        payload = fetch_payload(mode, api_key, max(1.0, min(args.timeout, 120.0)))
    industries = parse_payload(payload, mode)
    ranked = sorted(industries, key=lambda item: item["momentum_score"], reverse=True)
    result = {
        "ok": True,
        "source": f"FINVIZ-{mode}",
        "industry_count": len(ranked),
        "leaders": ranked[:top],
        "laggards": list(reversed(ranked[-top:])),
    }
    target = workspace_output(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["output_path"] = str(target)
    emit("finish", "theme_detect", source=f"FINVIZ-{mode}", duration_seconds=round(time.time() - started, 3))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
