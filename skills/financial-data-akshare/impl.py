import json
import math
import re
from datetime import date, datetime
from decimal import Decimal

from core.env_utils import ensure_package_installed


SKILL_ID = "financial-data-akshare"
MAX_LIMIT = 500
DEFAULT_LIMIT = 50
DISCLAIMER = "AKShare data is for research/reference only and does not constitute investment advice."


def _json_response(payload):
    return json.dumps(_sanitize_json_value(payload), ensure_ascii=False, allow_nan=False, default=_json_default)


def _sanitize_json_value(value):
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {str(key): _sanitize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_value(item) for item in value]
    return value


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    try:
        import pandas as pd

        if value is pd.NA:
            return None
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
    except Exception:
        pass
    try:
        import numpy as np

        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            if math.isnan(float(value)) or math.isinf(float(value)):
                return None
            return float(value)
        if isinstance(value, np.ndarray):
            return value.tolist()
    except Exception:
        pass
    return str(value)


def _error_payload(function_name, error, **extra):
    payload = {
        "ok": False,
        "function_name": function_name or "",
        "akshare_version": "",
        "shape": None,
        "columns": [],
        "records": [],
        "truncated": False,
        "warning": DISCLAIMER,
        "error": error,
    }
    payload.update(extra)
    return payload


def _load_dependencies():
    ensure_package_installed("pandas", "pandas", skill_id=SKILL_ID)
    ensure_package_installed("akshare", "akshare", skill_id=SKILL_ID)
    import akshare as ak
    import pandas as pd

    return ak, pd


def _normalize_mapping(value):
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            raise ValueError("kwargs must be a JSON object.")
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("kwargs must be an object.")


def _normalize_columns(columns):
    if columns is None or columns == "":
        return []
    if isinstance(columns, str):
        text = columns.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                columns = parsed
            else:
                columns = [text]
        except Exception:
            columns = [item.strip() for item in text.split(",")]
    if not isinstance(columns, (list, tuple)):
        raise ValueError("columns must be a list of column names.")
    normalized = []
    for item in columns:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _normalize_limit(limit):
    try:
        value = int(limit)
    except Exception:
        value = DEFAULT_LIMIT
    if value <= 0:
        value = DEFAULT_LIMIT
    return min(value, MAX_LIMIT)


def _is_valid_function_name(function_name):
    text = str(function_name or "").strip()
    return bool(text) and not text.startswith("_") and re.match(r"^[A-Za-z][A-Za-z0-9_]*$", text)


def _to_dataframe(result, pd):
    if isinstance(result, pd.DataFrame):
        return result
    if isinstance(result, pd.Series):
        return result.to_frame().reset_index()
    if isinstance(result, (list, tuple, dict)):
        try:
            return pd.DataFrame(result)
        except Exception:
            return None
    return None


def _clean_dataframe(df, pd):
    cleaned = df.copy().astype(object)
    cleaned = cleaned.where(pd.notnull(cleaned), None)
    return cleaned


def query_akshare_data(function_name, kwargs=None, limit=DEFAULT_LIMIT, columns=None, include_columns=True):
    """
    Query an AKShare public function and return a structured JSON result.
    """
    function_name = str(function_name or "").strip()
    if not _is_valid_function_name(function_name):
        return _json_response(_error_payload(function_name, "invalid_function_name"))

    try:
        normalized_kwargs = _normalize_mapping(kwargs)
        selected_columns = _normalize_columns(columns)
        row_limit = _normalize_limit(limit)
    except Exception as exc:
        return _json_response(_error_payload(function_name, str(exc)))

    try:
        ak, pd = _load_dependencies()
    except Exception as exc:
        return _json_response(_error_payload(function_name, f"dependency_unavailable: {exc}"))

    akshare_version = str(getattr(ak, "__version__", "") or "")
    target = getattr(ak, function_name, None)
    if not callable(target) or function_name.startswith("_"):
        return _json_response(
            _error_payload(function_name, "akshare_function_not_found", akshare_version=akshare_version)
        )

    try:
        result = target(**normalized_kwargs)
    except Exception as exc:
        return _json_response(
            _error_payload(function_name, f"akshare_call_failed: {exc}", akshare_version=akshare_version)
        )

    df = _to_dataframe(result, pd)
    if df is None:
        return _json_response(
            {
                "ok": True,
                "function_name": function_name,
                "akshare_version": akshare_version,
                "shape": None,
                "columns": [],
                "records": [{"value": result}],
                "truncated": False,
                "warning": DISCLAIMER,
                "error": None,
            }
        )

    full_columns = [str(column) for column in df.columns]
    if selected_columns:
        missing = [column for column in selected_columns if column not in full_columns]
        if missing:
            return _json_response(
                _error_payload(
                    function_name,
                    "columns_not_found: " + ", ".join(missing),
                    akshare_version=akshare_version,
                    columns=full_columns,
                )
            )
        df = df[selected_columns]

    total_rows = int(len(df.index))
    truncated = total_rows > row_limit
    preview = _clean_dataframe(df.head(row_limit), pd)
    records = preview.to_dict(orient="records")

    return _json_response(
        {
            "ok": True,
            "function_name": function_name,
            "akshare_version": akshare_version,
            "shape": [total_rows, len(df.columns)],
            "columns": full_columns if include_columns else [],
            "records": records,
            "truncated": truncated,
            "warning": DISCLAIMER,
            "error": None,
        }
    )


TOOL_EXPORTS = [
    {
        "name": "query_akshare_data",
        "handler": query_akshare_data,
        "description": "Query a public AKShare financial-data function and return truncated structured JSON.",
        "parameters": {
            "type": "object",
            "properties": {
                "function_name": {
                    "type": "string",
                    "description": "AKShare public function name, for example stock_zh_a_spot_em.",
                },
                "kwargs": {
                    "type": "object",
                    "description": "Keyword arguments passed to the AKShare function.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum rows to return. Defaults to 50 and is capped at 500.",
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional column names to include in returned records.",
                },
                "include_columns": {
                    "type": "boolean",
                    "description": "Whether to include the full column list in the response.",
                },
            },
            "required": ["function_name"],
        },
        "aliases": ["akshare_query", "query_financial_data"],
        "search_hint": "akshare financial data stock fund index futures bond macro market quotes",
        "read_only": True,
        "destructive": False,
        "allowed_modes": ["clarifying", "execution"],
        "should_defer": True,
        "result_format": "json",
    }
]
