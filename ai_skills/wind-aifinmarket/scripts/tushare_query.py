import json
import os
import re
import sys
import time
import urllib.error
import urllib.request


API_URL = "https://api.tushare.pro"


def diagnostic(stage, **fields):
    payload = {
        "type": "wind_aifinmarket_network",
        "stage": stage,
        "entry": "tushare_query",
        "timestamp": time.time(),
    }
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)


def fail(code, message):
    diagnostic("error", error_code=code)
    print(json.dumps({"ok": False, "error": {"code": code, "message": message}}, ensure_ascii=False))
    raise SystemExit(1)


def main():
    if len(sys.argv) < 3 or sys.argv[1] in {"-h", "--help"}:
        print("Usage: tushare_query.py <api_name> '<params_json>' [fields]")
        return
    diagnostic("submit")
    token = str(os.environ.get("TUSHARE_TOKEN") or "").strip()
    if not token:
        fail("AUTH_ERROR", "TUSHARE_TOKEN is not configured in the Cowork capability center.")
    api_name = str(sys.argv[1] or "").strip()
    if not api_name or not api_name.replace("_", "").isalnum():
        fail("INVALID_API_NAME", "api_name must contain only letters, digits, and underscores.")
    try:
        params = json.loads(sys.argv[2])
    except Exception as exc:
        fail("INVALID_PARAMS_JSON", str(exc))
    if not isinstance(params, dict):
        fail("INVALID_PARAMS", "params_json must decode to an object.")
    fields = str(sys.argv[3] if len(sys.argv) > 3 else "").strip()
    body = json.dumps(
        {"api_name": api_name, "token": token, "params": params, "fields": fields},
        ensure_ascii=False,
    ).encode("utf-8")
    diagnostic("start", api_name=api_name)
    request = urllib.request.Request(API_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        diagnostic("run", api_name=api_name)
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            fail("AUTH_ERROR", f"Tushare authentication failed with HTTP {exc.code}.")
        if exc.code == 429:
            fail("QUOTA_ERROR", "Tushare request quota or rate limit was exceeded.")
        fail("HTTP_ERROR", f"HTTP {exc.code}")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            fail("TIMEOUT", "Tushare request timed out.")
        fail("NETWORK_ERROR", str(exc.reason))
    except TimeoutError:
        fail("TIMEOUT", "Tushare request timed out.")
    if int(payload.get("code") or 0) != 0:
        message = str(payload.get("msg") or "Tushare returned an error.")
        if re.search(r"token|权限|认证|无效", message, re.IGNORECASE):
            fail("AUTH_ERROR", message)
        if re.search(r"额度|积分|频繁|限流|quota|rate", message, re.IGNORECASE):
            fail("QUOTA_ERROR", message)
        fail("TUSHARE_ERROR", message)
    data = payload.get("data")
    if not data or (isinstance(data, dict) and not data.get("items")):
        fail("NO_RESULTS", "Tushare returned no data.")
    print(json.dumps({"ok": True, "api_name": api_name, "data": data}, ensure_ascii=False))
    diagnostic("finish", api_name=api_name)


if __name__ == "__main__":
    main()
