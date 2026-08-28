"""DeepSeek official request-cost estimation and account balance lookup."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx

from .llm.model_catalog import is_deepseek_official_base_url


DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"
DEEPSEEK_PRICING_VERSION = "2026-08-17"
DEEPSEEK_PRICING_EFFECTIVE_AT = datetime(
    2026,
    8,
    17,
    0,
    0,
    tzinfo=timezone(timedelta(hours=8)),
).timestamp()
DEEPSEEK_PRICING_SOURCE = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
DEEPSEEK_BILLING_TIMEZONE = timezone(timedelta(hours=8))
SUPPORTED_BALANCE_CURRENCIES = ("CNY", "USD")

# Official prices per one million tokens.  The registry is deliberately
# versioned and exact-model-only: an unknown model must not inherit a price.
DEEPSEEK_PRICE_TABLE = {
    "deepseek-v4-flash": {
        "off_peak": {
            "CNY": (Decimal("0.05"), Decimal("1.5"), Decimal("4.5")),
            "USD": (Decimal("0.007"), Decimal("0.22"), Decimal("0.66")),
        },
        "peak": {
            "CNY": (Decimal("0.10"), Decimal("3.0"), Decimal("9.0")),
            "USD": (Decimal("0.014"), Decimal("0.44"), Decimal("1.32")),
        },
    },
    "deepseek-v4-pro": {
        "off_peak": {
            "CNY": (Decimal("0.15"), Decimal("4.5"), Decimal("13.5")),
            "USD": (Decimal("0.022"), Decimal("0.66"), Decimal("1.98")),
        },
        "peak": {
            "CNY": (Decimal("0.30"), Decimal("9.0"), Decimal("27.0")),
            "USD": (Decimal("0.044"), Decimal("1.32"), Decimal("3.96")),
        },
    },
}


class DeepSeekBalanceError(RuntimeError):
    """A safe, user-displayable balance lookup failure."""

    def __init__(self, category, message):
        super().__init__(str(message or "余额查询失败"))
        self.category = str(category or "unknown")


def _decimal_text(value):
    number = Decimal(value)
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _decimal_value(value, field_name):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise DeepSeekBalanceError("invalid_response", f"余额字段 {field_name} 不是有效数字") from exc
    if not number.is_finite():
        raise DeepSeekBalanceError("invalid_response", f"余额字段 {field_name} 不是有限数字")
    return number


def _usage_int(usage, key):
    if key not in usage or isinstance(usage.get(key), bool):
        return None
    try:
        value = int(usage.get(key))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _safe_nonnegative_int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def deepseek_pricing_period(started_at):
    """Return the official Beijing peak/off-peak period for a request."""

    try:
        timestamp = float(started_at)
    except (TypeError, ValueError):
        return ""
    if timestamp < DEEPSEEK_PRICING_EFFECTIVE_AT:
        return ""
    local_time = datetime.fromtimestamp(timestamp, DEEPSEEK_BILLING_TIMEZONE).time()
    minute_of_day = local_time.hour * 60 + local_time.minute
    if 9 * 60 <= minute_of_day < 12 * 60:
        return "peak"
    if 14 * 60 <= minute_of_day < 18 * 60:
        return "peak"
    return "off_peak"


def estimate_deepseek_request_cost(usage, started_at):
    """Estimate one official DeepSeek request from provider-returned usage."""

    source = dict(usage) if isinstance(usage, dict) else {}
    base_url = str(source.get("base_url") or "").strip()
    if not is_deepseek_official_base_url(base_url):
        return {"ok": False, "reason_code": "not_official", "reason": "不是 DeepSeek 官方接口"}
    model = str(source.get("model") or "").strip().lower()
    if model not in DEEPSEEK_PRICE_TABLE:
        return {"ok": False, "reason_code": "unknown_model", "reason": "当前模型没有已核验价格"}
    period = deepseek_pricing_period(started_at)
    if not period:
        return {"ok": False, "reason_code": "pricing_not_effective", "reason": "请求时间不在当前价格版本范围内"}

    input_tokens = _usage_int(source, "input_tokens")
    output_tokens = _usage_int(source, "output_tokens")
    cached_tokens = _usage_int(source, "cached_input_tokens")
    uncached_tokens = _usage_int(source, "uncached_input_tokens")
    cache_status = str(source.get("cache_metrics_status") or "").strip().lower()
    if input_tokens is None or output_tokens is None:
        return {"ok": False, "reason_code": "missing_usage", "reason": "输入或输出 token 用量缺失"}
    if cache_status in {"", "unavailable"}:
        return {"ok": False, "reason_code": "missing_cache_usage", "reason": "缓存 token 口径不可用"}
    if cached_tokens is None and uncached_tokens is None:
        return {"ok": False, "reason_code": "missing_cache_usage", "reason": "缓存命中与未命中 token 均缺失"}
    if cached_tokens is None:
        cached_tokens = input_tokens - uncached_tokens
    if uncached_tokens is None:
        uncached_tokens = input_tokens - cached_tokens
    if cached_tokens < 0 or uncached_tokens < 0 or cached_tokens + uncached_tokens != input_tokens:
        return {"ok": False, "reason_code": "inconsistent_usage", "reason": "输入 token 与缓存明细不一致"}

    amounts = {}
    one_million = Decimal("1000000")
    for currency in SUPPORTED_BALANCE_CURRENCIES:
        hit_price, miss_price, output_price = DEEPSEEK_PRICE_TABLE[model][period][currency]
        amount = (
            Decimal(cached_tokens) * hit_price
            + Decimal(uncached_tokens) * miss_price
            + Decimal(output_tokens) * output_price
        ) / one_million
        amounts[currency] = _decimal_text(amount)
    return {
        "ok": True,
        "model": model,
        "period": period,
        "started_at": float(started_at),
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cached_input_tokens": cached_tokens,
            "uncached_input_tokens": uncached_tokens,
        },
        "amounts": amounts,
        "pricing_version": DEEPSEEK_PRICING_VERSION,
        "pricing_source": DEEPSEEK_PRICING_SOURCE,
    }


def normalize_deepseek_balance(payload, fetched_at=None):
    if not isinstance(payload, dict):
        raise DeepSeekBalanceError("invalid_response", "余额接口没有返回 JSON 对象")
    raw_infos = payload.get("balance_infos")
    if not isinstance(raw_infos, list) or not raw_infos:
        raise DeepSeekBalanceError("invalid_response", "余额接口没有返回余额明细")
    infos = []
    seen = set()
    for raw in raw_infos:
        if not isinstance(raw, dict):
            raise DeepSeekBalanceError("invalid_response", "余额明细格式无效")
        currency = str(raw.get("currency") or "").strip().upper()
        if currency not in SUPPORTED_BALANCE_CURRENCIES or currency in seen:
            raise DeepSeekBalanceError("invalid_response", "余额币种无效或重复")
        seen.add(currency)
        info = {"currency": currency}
        for field in ("total_balance", "granted_balance", "topped_up_balance"):
            info[field] = _decimal_text(_decimal_value(raw.get(field), field))
        infos.append(info)
    try:
        normalized_fetched_at = float(time.time() if fetched_at is None else fetched_at)
    except (TypeError, ValueError) as exc:
        raise DeepSeekBalanceError("invalid_response", "余额查询时间无效") from exc
    return {
        "is_available": bool(payload.get("is_available")),
        "balance_infos": infos,
        "fetched_at": normalized_fetched_at,
    }


def fetch_deepseek_balance(api_key, timeout=6.0):
    """Perform one official balance request.  This function never retries."""

    secret = str(api_key or "").strip()
    if not secret:
        raise DeepSeekBalanceError("missing_key", "未配置 DeepSeek API Key")
    try:
        request_timeout = httpx.Timeout(float(timeout), connect=min(float(timeout), 4.0))
        with httpx.Client(timeout=request_timeout, follow_redirects=False) as client:
            response = client.get(
                DEEPSEEK_BALANCE_URL,
                headers={"Accept": "application/json", "Authorization": f"Bearer {secret}"},
            )
        if response.status_code in {401, 403}:
            raise DeepSeekBalanceError("authentication", "DeepSeek API Key 无法查询余额")
        if response.status_code != 200:
            raise DeepSeekBalanceError("http_status", f"余额接口返回 HTTP {response.status_code}")
        try:
            payload = response.json()
        except Exception as exc:
            raise DeepSeekBalanceError("invalid_response", "余额接口返回了无效 JSON") from exc
        return normalize_deepseek_balance(payload)
    except DeepSeekBalanceError:
        raise
    except httpx.TimeoutException as exc:
        raise DeepSeekBalanceError("timeout", "余额查询超时") from exc
    except httpx.RequestError as exc:
        raise DeepSeekBalanceError("network", "无法连接 DeepSeek 余额接口") from exc
    except Exception as exc:
        raise DeepSeekBalanceError("unknown", "余额查询失败") from exc


def normalize_deepseek_billing_snapshot(snapshot):
    """Normalize the non-secret billing state stored in conversation metadata."""

    if not isinstance(snapshot, dict):
        return {}
    run_id = str(snapshot.get("run_id") or "").strip()
    if not run_id:
        return {}
    normalized = {
        "run_id": run_id,
        "profile_id": str(snapshot.get("profile_id") or "").strip(),
        "model": str(snapshot.get("model") or "").strip(),
        "cost_status": str(snapshot.get("cost_status") or "unavailable").strip(),
        "cost_reason_code": str(snapshot.get("cost_reason_code") or "").strip(),
        "pricing_version": str(snapshot.get("pricing_version") or "").strip(),
        "pricing_periods": [
            period for period in (
                snapshot.get("pricing_periods")
                if isinstance(snapshot.get("pricing_periods"), list)
                else []
            )
            if period in {"peak", "off_peak"}
        ],
        "balance_status": str(snapshot.get("balance_status") or "not_recorded").strip(),
    }
    for field in ("completed_at",):
        try:
            normalized[field] = float(snapshot.get(field) or 0.0)
        except (TypeError, ValueError):
            normalized[field] = 0.0
    usage = snapshot.get("usage") if isinstance(snapshot.get("usage"), dict) else {}
    normalized["usage"] = {
        key: _safe_nonnegative_int(usage.get(key))
        for key in (
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "uncached_input_tokens",
            "request_count",
        )
    }
    amounts = snapshot.get("costs") if isinstance(snapshot.get("costs"), dict) else {}
    normalized["costs"] = {}
    for currency in SUPPORTED_BALANCE_CURRENCIES:
        if currency not in amounts:
            continue
        try:
            normalized["costs"][currency] = _decimal_text(_decimal_value(amounts[currency], currency))
        except DeepSeekBalanceError:
            continue
    balance = snapshot.get("balance")
    if normalized["balance_status"] == "succeeded" and isinstance(balance, dict):
        try:
            normalized["balance"] = normalize_deepseek_balance(
                balance,
                fetched_at=balance.get("fetched_at"),
            )
        except DeepSeekBalanceError:
            normalized["balance_status"] = "not_recorded"
    if str(snapshot.get("balance_error_category") or "").strip():
        normalized["balance_error_category"] = str(snapshot.get("balance_error_category") or "").strip()
    return normalized


def billing_snapshot_for_persistence(snapshot):
    normalized = normalize_deepseek_billing_snapshot(snapshot)
    if not normalized:
        return {}
    if normalized.get("balance_status") != "succeeded":
        normalized["balance_status"] = "not_recorded"
        normalized.pop("balance", None)
        normalized.pop("balance_error_category", None)
    return normalized
