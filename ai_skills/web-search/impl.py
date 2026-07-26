import json
import logging
import re
import threading
import time
from urllib.parse import urlparse, urlunparse

import requests

from core.env_utils import ensure_package_installed
from core.app_version import APP_VERSION


ANYSEARCH_API_URL = "https://api.anysearch.com/mcp"
ANYSEARCH_REGISTER_URL = "https://api.anysearch.com/v1/auth/email/register"
ANYSEARCH_CLIENT_HEADER = "skill/3.0.1"
COWORK_CLIENT_HEADER = f"cowork/{APP_VERSION}"
DEFAULT_PROVIDER = "anysearch"
SUPPORTED_PROVIDERS = {"anysearch", "tavily"}
DEFAULT_TIMEOUT = 30
SEARCH_CACHE_TTL_SECONDS = 180
ARTICLE_CACHE_TTL_SECONDS = 300
MAX_SNIPPET_LENGTH = 640
CONTENT_PREVIEW_LENGTH = 240

logger = logging.getLogger("cowork.web_search")
_CACHE_LOCK = threading.Lock()
_SEARCH_CACHE = {}
_ARTICLE_CACHE = {}


class ProviderError(RuntimeError):
    def __init__(self, code, message=None, **details):
        super().__init__(message or code)
        self.code = str(code or "provider_error")
        self.details = details


def _now_ms():
    return int(time.time() * 1000)


def _json_response(payload):
    return json.dumps(payload, ensure_ascii=False)


def _log_event(event, *, provider="", status="", duration_ms=None, error_code=""):
    fields = [
        f"event={event}",
        f"provider={provider or 'none'}",
        f"status={status or 'unknown'}",
    ]
    if duration_ms is not None:
        fields.append(f"duration_ms={int(duration_ms)}")
    if error_code:
        fields.append(f"error_code={error_code}")
    logger.info("web_search %s", " ".join(fields))


def _truncate(text, max_chars):
    value = str(text or "")
    if max_chars is None or max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "…"


def _clean_text(text):
    cleaned = re.sub(r"\r\n?", "\n", str(text or ""))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _normalize_url(url):
    text = str(url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.scheme:
        text = "https://" + text
        parsed = urlparse(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def _normalize_domain(domain):
    text = str(domain or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        text = urlparse(text).netloc.lower()
    text = text.split("/")[0].strip(".")
    if text.startswith("www."):
        text = text[4:]
    return text


def _domain_from_url(url):
    return _normalize_domain(urlparse(str(url or "")).netloc)


def _normalize_domains(domains):
    normalized = []
    for item in domains or []:
        domain = _normalize_domain(item)
        if domain and domain not in normalized:
            normalized.append(domain)
    return normalized


def _domain_matches(domain, filters):
    return any(domain == item or domain.endswith("." + item) for item in filters)


def _apply_domain_filters(results, allowed_domains, blocked_domains):
    filtered = []
    for result in results:
        domain = result.get("domain") or _domain_from_url(result.get("url"))
        if allowed_domains and not _domain_matches(domain, allowed_domains):
            continue
        if blocked_domains and _domain_matches(domain, blocked_domains):
            continue
        filtered.append(result)
    return filtered


def _build_cache_key(*parts):
    return json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)


def _get_cache(cache, key, ttl_seconds):
    now = time.time()
    with _CACHE_LOCK:
        cached = cache.get(key)
        if not cached:
            return None
        expires_at, payload = cached
        if expires_at < now:
            cache.pop(key, None)
            return None
        return json.loads(json.dumps(payload, ensure_ascii=False))


def _set_cache(cache, key, ttl_seconds, payload):
    with _CACHE_LOCK:
        cache[key] = (
            time.time() + ttl_seconds,
            json.loads(json.dumps(payload, ensure_ascii=False)),
        )


def _skill_config(_context):
    context = _context if isinstance(_context, dict) else {}
    values = context.get("skill_config")
    return values if isinstance(values, dict) else {}


def _resolve_provider(provider, _context):
    selected = str(provider or _skill_config(_context).get("SEARCH_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    if selected not in SUPPORTED_PROVIDERS:
        raise ProviderError("unsupported_provider", f"Unsupported web-search provider: {selected}")
    return selected


def _api_key_for(provider, _context):
    key_name = "ANYSEARCH_API_KEY" if provider == "anysearch" else "TAVILY_API_KEY"
    context = _context if isinstance(_context, dict) else {}
    env_values = context.get("skill_config_env")
    if isinstance(env_values, dict) and env_values.get(key_name):
        return str(env_values.get(key_name) or "").strip()
    return str(_skill_config(_context).get(key_name) or "").strip()


def _auth_mode(api_key):
    return "api_key" if api_key else "keyless"


def _alternative_provider(provider):
    return "tavily" if provider == "anysearch" else "anysearch"


def _error_payload(error, *, provider="", started_at=None, **extra):
    code = error.code if isinstance(error, ProviderError) else "provider_error"
    message = str(error)
    details = dict(error.details) if isinstance(error, ProviderError) else {}
    payload = {
        "ok": False,
        "provider_used": provider,
        "error": code,
        "error_message": message,
        "retryable_with": _alternative_provider(provider) if provider in SUPPORTED_PROVIDERS else "",
        "warnings": [],
        "duration_ms": _now_ms() - started_at if started_at is not None else 0,
    }
    payload.update(details)
    payload.update(extra)
    return payload


def _decode_text_payload(value):
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return text


def _extract_anysearch_content(payload):
    if not isinstance(payload, dict):
        raise ProviderError("invalid_response", "AnySearch returned a non-object response.")
    if payload.get("error"):
        error = payload.get("error") or {}
        if isinstance(error, dict):
            raise ProviderError(str(error.get("code") or "anysearch_error"), str(error.get("message") or error))
        raise ProviderError("anysearch_error", str(error))
    result = payload.get("result")
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            texts = [item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"]
            if texts:
                return _decode_text_payload("\n".join(str(item) for item in texts))
        return result
    return result if result is not None else payload


def _call_anysearch(tool_name, arguments, api_key):
    headers = {
        "Content-Type": "application/json",
        "X-Anysearch-Client": ANYSEARCH_CLIENT_HEADER,
        "X-Cowork-Client": COWORK_CLIENT_HEADER,
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    try:
        response = requests.post(
            ANYSEARCH_API_URL,
            json=request_body,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=False,
        )
    except requests.Timeout as exc:
        raise ProviderError("timeout", "AnySearch request timed out.") from exc
    except requests.RequestException as exc:
        raise ProviderError("connection_error", f"Unable to reach AnySearch: {exc}") from exc
    if 300 <= response.status_code < 400:
        raise ProviderError("unexpected_redirect", "AnySearch returned an unexpected redirect.")
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise ProviderError("http_error", f"AnySearch HTTP {response.status_code}.", status_code=response.status_code) from exc
    try:
        return _extract_anysearch_content(response.json())
    except ValueError as exc:
        raise ProviderError("invalid_json", "AnySearch returned invalid JSON.") from exc


def _get_tavily_client(api_key):
    try:
        ensure_package_installed(
            "tavily-python==0.7.26",
            "tavily",
            skill_id="web-search",
        )
        from tavily import TavilyClient
    except Exception as exc:
        raise ProviderError("dependency_unavailable", f"tavily-python is unavailable: {exc}") from exc
    try:
        return TavilyClient(api_key=api_key) if api_key else TavilyClient()
    except Exception as exc:
        raise ProviderError("client_initialization_failed", f"Unable to initialize Tavily: {exc}") from exc


def _call_tavily_search(query, max_results, allowed_domains, blocked_domains, api_key):
    client = _get_tavily_client(api_key)
    try:
        return client.search(
            query=query,
            max_results=max(1, min(int(max_results), 20)),
            include_domains=allowed_domains or None,
            exclude_domains=blocked_domains or None,
            include_answer=False,
            include_raw_content=False,
        )
    except Exception as exc:
        code = getattr(exc, "code", "") or exc.__class__.__name__
        details = {}
        retry_after = getattr(exc, "retry_after_seconds", None)
        if retry_after is not None:
            details["retry_after_seconds"] = retry_after
        raise ProviderError(str(code), str(exc), **details) from exc


def _call_tavily_extract(url, api_key):
    client = _get_tavily_client(api_key)
    try:
        return client.extract(urls=[url], format="markdown")
    except TypeError:
        try:
            return client.extract(urls=[url])
        except Exception as exc:
            raise ProviderError(exc.__class__.__name__, str(exc)) from exc
    except Exception as exc:
        code = getattr(exc, "code", "") or exc.__class__.__name__
        details = {}
        retry_after = getattr(exc, "retry_after_seconds", None)
        if retry_after is not None:
            details["retry_after_seconds"] = retry_after
        raise ProviderError(str(code), str(exc), **details) from exc


def _find_result_items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("results", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _find_result_items(value)
            if nested:
                return nested
    return []


def _standardize_result(item, provider):
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or item.get("name") or item.get("heading") or "").strip()
    url = _normalize_url(item.get("url") or item.get("href") or item.get("link"))
    snippet = _clean_text(
        item.get("content")
        or item.get("snippet")
        or item.get("description")
        or item.get("body")
        or ""
    )
    if not url:
        return None
    domain = _domain_from_url(url)
    if not title:
        title = domain or url
    result = {
        "title": _truncate(title, 200),
        "url": url,
        "snippet": _truncate(snippet, MAX_SNIPPET_LENGTH),
        "domain": domain,
        "provider": provider,
    }
    if item.get("score") is not None:
        result["score"] = item.get("score")
    return result


def _standardize_results(payload, provider, max_results):
    results = []
    seen = set()
    for item in _find_result_items(payload):
        standardized = _standardize_result(item, provider)
        if not standardized or standardized["url"] in seen:
            continue
        seen.add(standardized["url"])
        results.append(standardized)
        if len(results) >= max_results:
            break
    return results


def _unsupported_location_error(region, language):
    values = []
    if region:
        values.append("region")
    if language:
        values.append("language")
    if values:
        raise ProviderError(
            "unsupported_parameter",
            "The selected provider does not support these compatibility parameters: " + ", ".join(values),
            unsupported_parameters=values,
        )


def search_web(
    query,
    max_results=8,
    allowed_domains=None,
    blocked_domains=None,
    region=None,
    language=None,
    provider=None,
    domain=None,
    sub_domain=None,
    sub_domain_params=None,
    _context=None,
):
    started_at = _now_ms()
    query = str(query or "").strip()
    selected = ""
    _log_event("search_submit", status="submitted")
    if not query:
        return _json_response(
            _error_payload(
                ProviderError("query_required", "Search query is required."),
                started_at=started_at,
                query=query,
                results=[],
                sources=[],
            )
        )
    allowed_domains = _normalize_domains(allowed_domains)
    blocked_domains = _normalize_domains(blocked_domains)
    if allowed_domains and blocked_domains:
        return _json_response(
            _error_payload(
                ProviderError("allowed_and_blocked_domains_conflict", "allowed_domains and blocked_domains are mutually exclusive."),
                started_at=started_at,
                query=query,
                results=[],
                sources=[],
            )
        )
    try:
        selected = _resolve_provider(provider, _context)
        _unsupported_location_error(region, language)
        if selected == "tavily" and any([domain, sub_domain, sub_domain_params]):
            raise ProviderError(
                "unsupported_parameter",
                "Tavily does not support AnySearch vertical-domain parameters.",
                unsupported_parameters=["domain", "sub_domain", "sub_domain_params"],
            )
        api_key = _api_key_for(selected, _context)
        auth_mode = _auth_mode(api_key)
        cache_key = _build_cache_key(
            selected,
            auth_mode,
            query,
            max_results,
            allowed_domains,
            blocked_domains,
            domain,
            sub_domain,
            sub_domain_params,
        )
        cached = _get_cache(_SEARCH_CACHE, cache_key, SEARCH_CACHE_TTL_SECONDS)
        if cached is not None:
            cached["warnings"] = list(cached.get("warnings") or []) + ["cache_hit"]
            return _json_response(cached)
        _log_event("search_start", provider=selected, status="started")
        if selected == "anysearch":
            arguments = {"query": query, "max_results": max(1, min(int(max_results), 10))}
            if domain:
                arguments["domain"] = str(domain)
            if sub_domain:
                arguments["sub_domain"] = str(sub_domain)
            if sub_domain_params is not None:
                arguments["sub_domain_params"] = sub_domain_params
            raw = _call_anysearch("search", arguments, api_key)
            results = _standardize_results(raw, selected, max(1, min(int(max_results), 10)))
            results = _apply_domain_filters(results, allowed_domains, blocked_domains)
        else:
            raw = _call_tavily_search(query, max_results, allowed_domains, blocked_domains, api_key)
            results = _standardize_results(raw, selected, max(1, min(int(max_results), 20)))
        if not results:
            raise ProviderError("no_results", f"{selected} returned no usable search results.")
        payload = {
            "ok": True,
            "query": query,
            "provider_used": selected,
            "auth_mode": auth_mode,
            "results": results,
            "sources": [item["url"] for item in results],
            "warnings": ["keyless_rate_limits_apply"] if auth_mode == "keyless" else [],
            "error": None,
            "duration_ms": _now_ms() - started_at,
        }
        _set_cache(_SEARCH_CACHE, cache_key, SEARCH_CACHE_TTL_SECONDS, payload)
        _log_event("search_finish", provider=selected, status="success", duration_ms=payload["duration_ms"])
        return _json_response(payload)
    except Exception as exc:
        error = exc if isinstance(exc, ProviderError) else ProviderError("search_failed", str(exc))
        payload = _error_payload(
            error,
            provider=selected,
            started_at=started_at,
            query=query,
            results=[],
            sources=[],
        )
        _log_event("search_error", provider=selected, status="error", duration_ms=payload["duration_ms"], error_code=payload["error"])
        return _json_response(payload)


def _extract_article_content(payload, provider, url):
    if provider == "tavily":
        items = _find_result_items(payload)
        if not items:
            failed = payload.get("failed_results") if isinstance(payload, dict) else None
            raise ProviderError("extract_failed", "Tavily did not return extracted content.", failed_results=failed or [])
        item = items[0]
        content = item.get("raw_content") or item.get("content") or ""
        final_url = _normalize_url(item.get("url") or url) or url
        title = str(item.get("title") or "").strip()
    elif isinstance(payload, str):
        content = payload
        final_url = url
        title = ""
    elif isinstance(payload, dict):
        content = payload.get("content") or payload.get("markdown") or payload.get("raw_content") or ""
        final_url = _normalize_url(payload.get("url") or payload.get("final_url") or url) or url
        title = str(payload.get("title") or "").strip()
    else:
        content = ""
        final_url = url
        title = ""
    content = _clean_text(content)
    if not content:
        raise ProviderError("empty_content", f"{provider} returned empty article content.")
    return content, final_url, title


def read_web_article(url, max_chars=12000, provider=None, _context=None):
    started_at = _now_ms()
    selected = ""
    normalized = _normalize_url(url)
    _log_event("extract_submit", status="submitted")
    if not normalized:
        return _json_response(
            _error_payload(
                ProviderError("invalid_url", "A valid HTTP(S) URL is required."),
                started_at=started_at,
                url=str(url or ""),
                content="",
            )
        )
    try:
        selected = _resolve_provider(provider, _context)
        api_key = _api_key_for(selected, _context)
        auth_mode = _auth_mode(api_key)
        cache_key = _build_cache_key(selected, auth_mode, normalized, max_chars)
        cached = _get_cache(_ARTICLE_CACHE, cache_key, ARTICLE_CACHE_TTL_SECONDS)
        if cached is not None:
            cached["warnings"] = list(cached.get("warnings") or []) + ["cache_hit"]
            return _json_response(cached)
        _log_event("extract_start", provider=selected, status="started")
        raw = _call_anysearch("extract", {"url": normalized}, api_key) if selected == "anysearch" else _call_tavily_extract(normalized, api_key)
        content, final_url, title = _extract_article_content(raw, selected, normalized)
        content = _truncate(content, int(max_chars))
        payload = {
            "ok": True,
            "url": normalized,
            "final_url": final_url,
            "provider_used": selected,
            "extractor_used": selected,
            "auth_mode": auth_mode,
            "title": title,
            "content": content,
            "content_preview": _truncate(content, CONTENT_PREVIEW_LENGTH),
            "sources": [final_url],
            "warnings": ["keyless_rate_limits_apply"] if auth_mode == "keyless" else [],
            "error": None,
            "duration_ms": _now_ms() - started_at,
        }
        _set_cache(_ARTICLE_CACHE, cache_key, ARTICLE_CACHE_TTL_SECONDS, payload)
        _log_event("extract_finish", provider=selected, status="success", duration_ms=payload["duration_ms"])
        return _json_response(payload)
    except Exception as exc:
        error = exc if isinstance(exc, ProviderError) else ProviderError("extract_failed", str(exc))
        payload = _error_payload(
            error,
            provider=selected,
            started_at=started_at,
            url=normalized,
            content="",
            sources=[],
        )
        _log_event("extract_error", provider=selected, status="error", duration_ms=payload["duration_ms"], error_code=payload["error"])
        return _json_response(payload)


def read_article(url, max_chars=12000, provider=None, _context=None):
    return read_web_article(url, max_chars=max_chars, provider=provider, _context=_context)


def batch_search_web(queries, provider=None, _context=None):
    started_at = _now_ms()
    selected = ""
    try:
        selected = _resolve_provider(provider, _context)
        if selected != "anysearch":
            raise ProviderError("unsupported_provider", "batch_search_web is available only with AnySearch.")
        if not isinstance(queries, list) or not 1 <= len(queries) <= 5:
            raise ProviderError("invalid_queries", "queries must contain between 1 and 5 items.")
        normalized_queries = []
        for item in queries:
            if isinstance(item, str):
                item = {"query": item}
            if not isinstance(item, dict) or not str(item.get("query") or "").strip():
                raise ProviderError("invalid_queries", "Every batch item must contain a non-empty query.")
            normalized_queries.append(dict(item))
        api_key = _api_key_for(selected, _context)
        _log_event("batch_search_start", provider=selected, status="started")
        raw = _call_anysearch("batch_search", {"queries": normalized_queries}, api_key)
        payload = {
            "ok": True,
            "provider_used": selected,
            "auth_mode": _auth_mode(api_key),
            "queries": normalized_queries,
            "results": raw,
            "warnings": ["keyless_rate_limits_apply"] if not api_key else [],
            "error": None,
            "duration_ms": _now_ms() - started_at,
        }
        _log_event("batch_search_finish", provider=selected, status="success", duration_ms=payload["duration_ms"])
        return _json_response(payload)
    except Exception as exc:
        error = exc if isinstance(exc, ProviderError) else ProviderError("batch_search_failed", str(exc))
        payload = _error_payload(error, provider=selected, started_at=started_at, results=[])
        _log_event("batch_search_error", provider=selected, status="error", duration_ms=payload["duration_ms"], error_code=payload["error"])
        return _json_response(payload)


def get_web_search_sub_domains(domain=None, domains=None, provider=None, _context=None):
    started_at = _now_ms()
    selected = ""
    try:
        selected = _resolve_provider(provider, _context)
        if selected != "anysearch":
            raise ProviderError("unsupported_provider", "Vertical-domain discovery is available only with AnySearch.")
        if bool(domain) == bool(domains):
            raise ProviderError("domain_required", "Provide exactly one of domain or domains.")
        arguments = {"domain": str(domain).strip()} if domain else {"domains": list(domains)}
        api_key = _api_key_for(selected, _context)
        _log_event("sub_domains_start", provider=selected, status="started")
        raw = _call_anysearch("get_sub_domains", arguments, api_key)
        payload = {
            "ok": True,
            "provider_used": selected,
            "auth_mode": _auth_mode(api_key),
            "sub_domains": raw,
            "warnings": ["keyless_rate_limits_apply"] if not api_key else [],
            "error": None,
            "duration_ms": _now_ms() - started_at,
        }
        _log_event("sub_domains_finish", provider=selected, status="success", duration_ms=payload["duration_ms"])
        return _json_response(payload)
    except Exception as exc:
        error = exc if isinstance(exc, ProviderError) else ProviderError("sub_domains_failed", str(exc))
        payload = _error_payload(error, provider=selected, started_at=started_at, sub_domains=[])
        _log_event("sub_domains_error", provider=selected, status="error", duration_ms=payload["duration_ms"], error_code=payload["error"])
        return _json_response(payload)


def _registration_error(message, started_at, **extra):
    text = str(message or "").strip()
    if text == "Invalid email address.":
        code = "invalid_email"
    elif text == "email_already_registered":
        code = "email_already_registered"
    elif "Rate limited" in text:
        code = "rate_limited"
        match = re.search(r"(\d+)\s*seconds", text)
        if match:
            extra["retry_after_seconds"] = int(match.group(1))
    elif text.startswith("Key creation failed."):
        code = "key_creation_failed"
    elif text == "Internal server error.":
        code = "internal_server_error"
    else:
        code = "registration_failed"
    payload = {
        "ok": False,
        "provider_used": "anysearch",
        "error": code,
        "error_message": text or "AnySearch registration failed.",
        "duration_ms": _now_ms() - started_at,
    }
    payload.update(extra)
    return payload


def register_anysearch_api_key(email, user_confirmed=False, _context=None):
    started_at = _now_ms()
    _log_event("registration_submit", provider="anysearch", status="submitted")
    if user_confirmed is not True:
        return _json_response(
            {
                "ok": False,
                "provider_used": "anysearch",
                "error": "user_confirmation_required",
                "error_message": "Ask the user to confirm account creation and local API-key storage before retrying.",
                "duration_ms": _now_ms() - started_at,
            }
        )
    address = str(email or "").strip()
    if not address:
        return _json_response(_registration_error("Invalid email address.", started_at))
    _log_event("registration_start", provider="anysearch", status="started")
    try:
        response = requests.post(
            ANYSEARCH_REGISTER_URL,
            json={"email": address},
            headers={
                "Content-Type": "application/json",
                "X-Anysearch-Client": ANYSEARCH_CLIENT_HEADER,
                "X-Cowork-Client": COWORK_CLIENT_HEADER,
            },
            timeout=DEFAULT_TIMEOUT,
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise ProviderError("unexpected_redirect", "AnySearch registration returned an unexpected redirect.")
        try:
            payload = response.json()
        except ValueError:
            response.raise_for_status()
            raise ProviderError("invalid_json", "AnySearch registration returned invalid JSON.")
        if not isinstance(payload, dict):
            raise ProviderError("invalid_response", "AnySearch registration returned a non-object response.")
        if response.status_code >= 400 and payload.get("code") not in {0, -1}:
            response.raise_for_status()
    except requests.Timeout:
        result = _registration_error("Registration request timed out.", started_at)
        _log_event("registration_error", provider="anysearch", status="error", duration_ms=result["duration_ms"], error_code=result["error"])
        return _json_response(result)
    except (requests.RequestException, ValueError, ProviderError) as exc:
        result = _registration_error(str(exc), started_at)
        _log_event("registration_error", provider="anysearch", status="error", duration_ms=result["duration_ms"], error_code=result["error"])
        return _json_response(result)
    if payload.get("code") != 0:
        result = _registration_error(payload.get("message"), started_at)
        _log_event("registration_error", provider="anysearch", status="error", duration_ms=result["duration_ms"], error_code=result["error"])
        return _json_response(result)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    key_data = data.get("api_key") if isinstance(data.get("api_key"), dict) else {}
    api_key = str(key_data.get("key") or "").strip()
    if not api_key:
        result = _registration_error("Key creation failed.", started_at, login_url=data.get("login_url") or "https://www.anysearch.com/login")
        _log_event("registration_error", provider="anysearch", status="error", duration_ms=result["duration_ms"], error_code=result["error"])
        return _json_response(result)
    context = _context if isinstance(_context, dict) else {}
    skill_manager = context.get("skill_manager")
    config_manager = getattr(skill_manager, "config_manager", None)
    if config_manager is None or not hasattr(config_manager, "get_skill_config") or not hasattr(config_manager, "set_skill_config"):
        result = _registration_error(
            "The account was created, but local Skill configuration is unavailable. Sign in to create a new key.",
            started_at,
            login_url=data.get("login_url") or "https://www.anysearch.com/login",
        )
        _log_event("registration_error", provider="anysearch", status="error", duration_ms=result["duration_ms"], error_code="config_unavailable")
        return _json_response(result)
    try:
        values = config_manager.get_skill_config("web-search")
        values["ANYSEARCH_API_KEY"] = api_key
        config_manager.set_skill_config("web-search", values)
        saved = config_manager.get_skill_config("web-search")
        if str(saved.get("ANYSEARCH_API_KEY") or "") != api_key:
            raise RuntimeError("Saved API key could not be verified.")
        publisher = context.get("skill_change_publisher")
        if callable(publisher):
            publisher(
                {
                    "action": "updated",
                    "skill_names": ["web-search"],
                    "source": "ai",
                    "session_id": context.get("session_id") or "",
                }
            )
    except Exception:
        result = _registration_error(
            "The account was created, but the API key could not be saved locally. Sign in to create a new key.",
            started_at,
            login_url=data.get("login_url") or "https://www.anysearch.com/login",
        )
        _log_event("registration_error", provider="anysearch", status="error", duration_ms=result["duration_ms"], error_code="config_save_failed")
        return _json_response(result)
    result = {
        "ok": True,
        "provider_used": "anysearch",
        "username": str(data.get("username") or address),
        "login_url": str(data.get("login_url") or "https://www.anysearch.com/login"),
        "key_prefix": str(key_data.get("key_prefix") or _truncate(api_key, 12)),
        "saved": True,
        "message": "API Key 已安全保存到网页搜索能力配置。随机密码和验证邮件已发送，请检查收件箱及垃圾邮件。",
        "duration_ms": _now_ms() - started_at,
    }
    _log_event("registration_finish", provider="anysearch", status="success", duration_ms=result["duration_ms"])
    return _json_response(result)


TOOL_EXPORTS = [
    {
        "name": "search_web",
        "handler": search_web,
        "description": "Search the web with the configured AnySearch or Tavily provider and return normalized JSON.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "max_results": {"type": "integer", "description": "Maximum number of results."},
                "allowed_domains": {"type": "array", "items": {"type": "string"}, "description": "Optional allowed domains."},
                "blocked_domains": {"type": "array", "items": {"type": "string"}, "description": "Optional blocked domains."},
                "region": {"type": "string", "description": "Compatibility parameter; unsupported providers return an explicit error."},
                "language": {"type": "string", "description": "Compatibility parameter; unsupported providers return an explicit error."},
                "provider": {"type": "string", "enum": ["anysearch", "tavily"], "description": "Optional per-call provider override."},
                "domain": {"type": "string", "description": "AnySearch vertical domain."},
                "sub_domain": {"type": "string", "description": "AnySearch vertical sub-domain."},
                "sub_domain_params": {"type": "object", "description": "AnySearch vertical sub-domain parameters."},
            },
            "required": ["query"],
        },
        "read_only": True,
        "search_hint": "web search internet anysearch tavily current information",
        "result_format": "json",
    },
    {
        "name": "read_web_article",
        "handler": read_web_article,
        "description": "Extract an online article with AnySearch or Tavily and return normalized markdown-oriented JSON.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Article URL."},
                "max_chars": {"type": "integer", "description": "Maximum characters to return."},
                "provider": {"type": "string", "enum": ["anysearch", "tavily"], "description": "Optional per-call provider override."},
            },
            "required": ["url"],
        },
        "read_only": True,
        "search_hint": "read extract web article url markdown anysearch tavily",
        "result_format": "json",
    },
    {
        "name": "batch_search_web",
        "handler": batch_search_web,
        "description": "Run 1-5 searches in one AnySearch batch request.",
        "parameters": {
            "type": "object",
            "properties": {
                "queries": {"type": "array", "items": {"type": ["object", "string"]}},
                "provider": {"type": "string", "enum": ["anysearch"]},
            },
            "required": ["queries"],
        },
        "read_only": True,
        "search_hint": "anysearch batch parallel web search",
        "result_format": "json",
    },
    {
        "name": "get_web_search_sub_domains",
        "handler": get_web_search_sub_domains,
        "description": "Discover AnySearch vertical sub-domains and their required parameters.",
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {"type": "string"},
                "domains": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
                "provider": {"type": "string", "enum": ["anysearch"]},
            },
        },
        "read_only": True,
        "search_hint": "anysearch vertical domain subdomain discovery",
        "result_format": "json",
    },
    {
        "name": "register_anysearch_api_key",
        "handler": register_anysearch_api_key,
        "description": "After explicit user confirmation, register an AnySearch account by email and save the one-time API key locally without returning it.",
        "parameters": {
            "type": "object",
            "properties": {
                "email": {"type": "string", "description": "A real email address that can receive the generated password."},
                "user_confirmed": {"type": "boolean", "description": "Must be true only after the user explicitly approves account creation and local key storage."},
            },
            "required": ["email", "user_confirmed"],
        },
        "read_only": False,
        "destructive": True,
        "requires_user_interaction": True,
        "search_hint": "register anysearch account obtain api key email",
        "result_format": "json",
    },
]
