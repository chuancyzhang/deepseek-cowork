import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx

from core.deepseek_billing import (
    DEEPSEEK_PRICING_EFFECTIVE_AT,
    DeepSeekBalanceError,
    billing_snapshot_for_persistence,
    deepseek_pricing_period,
    estimate_deepseek_request_cost,
    fetch_deepseek_balance,
    normalize_deepseek_balance,
    normalize_deepseek_billing_snapshot,
)


BEIJING = timezone(timedelta(hours=8))


def _timestamp(hour, minute=0):
    return datetime(2026, 8, 28, hour, minute, tzinfo=BEIJING).timestamp()


def _usage(model="deepseek-v4-flash", **overrides):
    payload = {
        "base_url": "https://api.deepseek.com/v1/",
        "model": model,
        "input_tokens": 1_000_000,
        "output_tokens": 100_000,
        "cached_input_tokens": 500_000,
        "uncached_input_tokens": 500_000,
        "cache_metrics_status": "deepseek_prompt_cache",
    }
    payload.update(overrides)
    return payload


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


class _FakeClient:
    def __init__(self, response=None, error=None, calls=None, **_kwargs):
        self.response = response
        self.error = error
        self.calls = calls if calls is not None else []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, url, headers=None):
        self.calls.append((url, dict(headers or {})))
        if self.error:
            raise self.error
        return self.response


class TestDeepSeekPricing(unittest.TestCase):
    def test_peak_boundaries_use_beijing_time(self):
        self.assertEqual(deepseek_pricing_period(_timestamp(8, 59)), "off_peak")
        self.assertEqual(deepseek_pricing_period(_timestamp(9, 0)), "peak")
        self.assertEqual(deepseek_pricing_period(_timestamp(11, 59)), "peak")
        self.assertEqual(deepseek_pricing_period(_timestamp(12, 0)), "off_peak")
        self.assertEqual(deepseek_pricing_period(_timestamp(14, 0)), "peak")
        self.assertEqual(deepseek_pricing_period(_timestamp(18, 0)), "off_peak")
        self.assertEqual(deepseek_pricing_period(DEEPSEEK_PRICING_EFFECTIVE_AT - 1), "")

    def test_flash_off_peak_cost_separates_cache_and_output(self):
        estimate = estimate_deepseek_request_cost(_usage(), _timestamp(8, 0))
        self.assertTrue(estimate["ok"])
        self.assertEqual(estimate["period"], "off_peak")
        self.assertEqual(estimate["amounts"], {"CNY": "1.225", "USD": "0.1795"})

    def test_flash_peak_cost_uses_peak_prices(self):
        estimate = estimate_deepseek_request_cost(_usage(), _timestamp(10, 0))
        self.assertTrue(estimate["ok"])
        self.assertEqual(estimate["amounts"], {"CNY": "2.45", "USD": "0.359"})

    def test_pro_cost_uses_exact_model_registry(self):
        estimate = estimate_deepseek_request_cost(
            _usage(model="deepseek-v4-pro"),
            _timestamp(8, 0),
        )
        self.assertTrue(estimate["ok"])
        self.assertEqual(estimate["amounts"], {"CNY": "3.675", "USD": "0.539"})

    def test_non_official_and_unknown_models_are_not_priced(self):
        non_official = estimate_deepseek_request_cost(
            _usage(base_url="https://deepseek.example.com/v1"),
            _timestamp(8, 0),
        )
        unknown = estimate_deepseek_request_cost(
            _usage(model="deepseek-next"),
            _timestamp(8, 0),
        )
        query_url = estimate_deepseek_request_cost(
            _usage(base_url="https://api.deepseek.com/v1?proxy=1"),
            _timestamp(8, 0),
        )
        abnormal_path = estimate_deepseek_request_cost(
            _usage(base_url="https://api.deepseek.com/compatible/v1"),
            _timestamp(8, 0),
        )
        self.assertEqual(non_official["reason_code"], "not_official")
        self.assertEqual(unknown["reason_code"], "unknown_model")
        self.assertEqual(query_url["reason_code"], "not_official")
        self.assertEqual(abnormal_path["reason_code"], "not_official")

    def test_missing_or_inconsistent_cache_usage_is_not_guessed(self):
        missing = estimate_deepseek_request_cost(
            _usage(cache_metrics_status="unavailable"),
            _timestamp(8, 0),
        )
        inconsistent = estimate_deepseek_request_cost(
            _usage(uncached_input_tokens=400_000),
            _timestamp(8, 0),
        )
        self.assertEqual(missing["reason_code"], "missing_cache_usage")
        self.assertEqual(inconsistent["reason_code"], "inconsistent_usage")


class TestDeepSeekBalance(unittest.TestCase):
    def test_balance_schema_accepts_cny_and_usd(self):
        balance = normalize_deepseek_balance(
            {
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "CNY",
                        "total_balance": "110.00",
                        "granted_balance": "10.00",
                        "topped_up_balance": "100.00",
                    },
                    {
                        "currency": "USD",
                        "total_balance": "2.50",
                        "granted_balance": "0",
                        "topped_up_balance": "2.5",
                    },
                ],
            },
            fetched_at=123.0,
        )
        self.assertTrue(balance["is_available"])
        self.assertEqual(balance["fetched_at"], 123.0)
        self.assertEqual(balance["balance_infos"][0]["total_balance"], "110")
        self.assertEqual(balance["balance_infos"][1]["currency"], "USD")

    def test_balance_schema_rejects_invalid_payload(self):
        with self.assertRaises(DeepSeekBalanceError) as caught:
            normalize_deepseek_balance({"is_available": True, "balance_infos": []})
        self.assertEqual(caught.exception.category, "invalid_response")

    def test_fetch_makes_one_request_and_does_not_expose_key(self):
        calls = []
        response = _FakeResponse(
            payload={
                "is_available": False,
                "balance_infos": [{
                    "currency": "CNY",
                    "total_balance": "0",
                    "granted_balance": "0",
                    "topped_up_balance": "0",
                }],
            }
        )
        with patch(
            "core.deepseek_billing.httpx.Client",
            side_effect=lambda **kwargs: _FakeClient(response=response, calls=calls, **kwargs),
        ) as client_class:
            result = fetch_deepseek_balance("secret-key", timeout=2)
        self.assertFalse(result["is_available"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(client_class.call_count, 1)
        self.assertEqual(calls[0][1]["Authorization"], "Bearer secret-key")
        self.assertNotIn("secret-key", str(result))

    def test_fetch_classifies_auth_timeout_network_and_invalid_json(self):
        request = httpx.Request("GET", "https://api.deepseek.com/user/balance")
        cases = [
            (_FakeResponse(status_code=401, payload={}), None, "authentication"),
            (None, httpx.ReadTimeout("late", request=request), "timeout"),
            (None, httpx.ConnectError("offline", request=request), "network"),
            (_FakeResponse(json_error=ValueError("bad json")), None, "invalid_response"),
        ]
        for response, error, category in cases:
            with self.subTest(category=category), patch(
                "core.deepseek_billing.httpx.Client",
                side_effect=lambda **kwargs: _FakeClient(response=response, error=error, **kwargs),
            ):
                with self.assertRaises(DeepSeekBalanceError) as caught:
                    fetch_deepseek_balance("secret-key")
                self.assertEqual(caught.exception.category, category)
                self.assertNotIn("secret-key", str(caught.exception))

    def test_failed_balance_details_are_not_persisted(self):
        snapshot = normalize_deepseek_billing_snapshot(
            {
                "run_id": "run-1",
                "profile_id": "profile-1",
                "model": "deepseek-v4-flash",
                "cost_status": "available",
                "costs": {"CNY": "0.0051"},
                "balance_status": "failed",
                "balance_error_category": "timeout",
            }
        )
        persisted = billing_snapshot_for_persistence(snapshot)
        self.assertEqual(persisted["balance_status"], "not_recorded")
        self.assertNotIn("balance_error_category", persisted)
        self.assertNotIn("balance", persisted)


if __name__ == "__main__":
    unittest.main()
