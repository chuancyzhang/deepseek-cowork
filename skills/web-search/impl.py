import json
import re
import threading
import time
import urllib.parse
from urllib.parse import urlparse, urlunparse

import requests

from core.env_utils import ensure_package_installed

DEFAULT_TIMEOUT = 12
MAX_RETRIES = 2
BACKOFF_SECONDS = 0.35
SEARCH_CACHE_TTL_SECONDS = 180
ARTICLE_CACHE_TTL_SECONDS = 300
MAX_SNIPPET_LENGTH = 320
CONTENT_PREVIEW_LENGTH = 240
MAX_RESULTS_PER_DOMAIN = 2
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_CACHE_LOCK = threading.Lock()
_SEARCH_CACHE = {}
_ARTICLE_CACHE = {}
_SESSION = None


def get_bs4():
    ensure_package_installed("beautifulsoup4", "bs4", skill_id="web-search")
    from bs4 import BeautifulSoup

    return BeautifulSoup


def get_ddgs():
    ensure_package_installed("duckduckgo-search", "duckduckgo_search", skill_id="web-search")
    from duckduckgo_search import DDGS

    return DDGS


def _get_session():
    global _SESSION
    if _SESSION is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        _SESSION = session
    return _SESSION


def _now_ms():
    return int(time.time() * 1000)


def _json_response(payload):
    return json.dumps(payload, ensure_ascii=False)


def _make_error_payload(error, **extra):
    payload = {"ok": False, "error": error}
    payload.update(extra)
    return payload


def _normalize_url(url):
    text = (url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if not parsed.scheme:
        text = "https://" + text
        parsed = urlparse(text)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def _normalize_domain(domain):
    text = (domain or "").strip().lower()
    if not text:
        return ""
    if "://" in text:
        text = urlparse(text).netloc.lower()
    text = text.split("/")[0].strip(".")
    if text.startswith("www."):
        text = text[4:]
    return text


def _domain_from_url(url):
    parsed = urlparse(url or "")
    return _normalize_domain(parsed.netloc)


def _normalize_domains(domains):
    normalized = []
    for item in domains or []:
        domain = _normalize_domain(item)
        if domain and domain not in normalized:
            normalized.append(domain)
    return normalized


def _build_cache_key(*parts):
    return json.dumps(parts, ensure_ascii=False, sort_keys=True)


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


def _request_text(url, *, timeout=DEFAULT_TIMEOUT):
    session = _get_session()
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.text, response.url
        except Exception as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(BACKOFF_SECONDS * (2**attempt))
    raise RuntimeError(str(last_error))


def _clean_text(text):
    cleaned = re.sub(r"\r\n?", "\n", text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _looks_like_html(text):
    sample = (text or "").strip().lower()
    if not sample:
        return False
    html_markers = ["<html", "<body", "<div", "<article", "<script", "</p>", "<!doctype"]
    return sum(1 for marker in html_markers if marker in sample[:3000]) >= 2


def _looks_like_block_page(text):
    sample = (text or "").lower()
    markers = [
        "access denied",
        "enable javascript",
        "captcha",
        "cloudflare",
        "verify you are human",
        "too many requests",
    ]
    return any(marker in sample for marker in markers)


def _truncate(text, max_chars):
    if max_chars is None or max_chars <= 0:
        return text
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _extract_title(text):
    if not text:
        return ""
    first_lines = [line.strip("# ").strip() for line in text.splitlines()[:5]]
    for line in first_lines:
        if len(line) >= 8:
            return _truncate(line, 160)
    return ""


def _make_article_result(url, *, final_url="", extractor_used="", title="", content="", warnings=None, error=None, duration_ms=0):
    content = _clean_text(content)
    return {
        "ok": error is None,
        "url": url,
        "final_url": final_url or url,
        "extractor_used": extractor_used,
        "title": title or _extract_title(content),
        "content": content,
        "content_preview": _truncate(content, CONTENT_PREVIEW_LENGTH) if content else "",
        "warnings": warnings or [],
        "duration_ms": duration_ms,
        "error": error,
    }


def _validate_article_text(text):
    cleaned = _clean_text(text)
    if len(cleaned) < 120:
        return False, "content_too_short", cleaned
    if _looks_like_block_page(cleaned):
        return False, "blocked_page", cleaned
    if _looks_like_html(cleaned):
        return False, "html_like_content", cleaned
    return True, None, cleaned


def _build_proxy_urls(url):
    normalized = _normalize_url(url)
    if not normalized:
        return []
    return [
        ("markdown.new", f"https://markdown.new/{normalized}"),
        ("defuddle.md", f"https://defuddle.md/{normalized}"),
        ("r.jina.ai", f"https://r.jina.ai/{normalized}"),
    ]


def _extract_with_scrapling(url):
    try:
        ensure_package_installed("scrapling", skill_id="web-search")
    except Exception as exc:
        raise RuntimeError(f"scrapling_dependency_unavailable: {exc}")
    import_attempts = [
        ("scrapling.fetchers", "Fetcher"),
        ("scrapling", "Fetcher"),
        ("scrapling", "Scraper"),
    ]
    for mod_name, cls_name in import_attempts:
        try:
            module = __import__(mod_name, fromlist=[cls_name])
            cls = getattr(module, cls_name, None)
            if cls is None:
                continue
            instance = cls()
            for method_name in ("get", "fetch", "request"):
                method = getattr(instance, method_name, None)
                if not callable(method):
                    continue
                result = method(url)
                candidates = [
                    getattr(result, "markdown", None),
                    getattr(result, "text", None),
                    getattr(result, "content", None),
                    str(result) if result is not None else None,
                ]
                for candidate in candidates:
                    if isinstance(candidate, bytes):
                        candidate = candidate.decode("utf-8", errors="replace")
                    if isinstance(candidate, str):
                        ok, _, cleaned = _validate_article_text(candidate)
                        if ok:
                            return cleaned
        except Exception:
            continue
    raise RuntimeError("scrapling_extraction_failed")


def _fetch_article_via_proxies(url):
    attempts = []
    for extractor_name, proxy_url in _build_proxy_urls(url):
        try:
            text, final_url = _request_text(proxy_url)
            ok, reason, cleaned = _validate_article_text(text)
            attempts.append({"extractor": extractor_name, "ok": ok, "reason": reason})
            if ok:
                return extractor_name, final_url, cleaned, attempts
        except Exception as exc:
            attempts.append({"extractor": extractor_name, "ok": False, "reason": str(exc)})
    return "", "", "", attempts


def _standardize_search_result(item, provider):
    title = ""
    url = ""
    snippet = ""
    if isinstance(item, dict):
        title = (item.get("title") or item.get("heading") or "").strip()
        url = item.get("href") or item.get("url") or ""
        snippet = (item.get("body") or item.get("snippet") or item.get("description") or "").strip()
    normalized_url = _normalize_url(url)
    domain = _domain_from_url(normalized_url)
    if not title or not normalized_url or not domain:
        return None
    return {
        "title": _truncate(title, 200),
        "url": normalized_url,
        "snippet": _truncate(_clean_text(snippet), MAX_SNIPPET_LENGTH),
        "domain": domain,
        "provider": provider,
    }


def _apply_domain_filters(results, allowed_domains, blocked_domains):
    filtered = []
    for result in results:
        domain = result["domain"]
        if allowed_domains and domain not in allowed_domains:
            continue
        if blocked_domains and domain in blocked_domains:
            continue
        filtered.append(result)
    return filtered


def _dedupe_results(results, max_results):
    deduped = []
    seen_urls = set()
    domain_counts = {}
    seen_title_domain = set()
    for result in results:
        url = result["url"]
        title_key = (result["domain"], result["title"].strip().lower())
        if url in seen_urls or title_key in seen_title_domain:
            continue
        count = domain_counts.get(result["domain"], 0)
        if count >= MAX_RESULTS_PER_DOMAIN:
            continue
        seen_urls.add(url)
        seen_title_domain.add(title_key)
        domain_counts[result["domain"]] = count + 1
        deduped.append(result)
        if len(deduped) >= max_results:
            break
    return deduped


def _search_duckduckgo(query, max_results, region=None):
    DDGS = get_ddgs()
    kwargs = {"max_results": max_results}
    if region:
        kwargs["region"] = region
    results = []
    with DDGS() as ddgs:
        for item in ddgs.text(query, **kwargs):
            standardized = _standardize_search_result(item, "duckduckgo")
            if standardized:
                results.append(standardized)
    return results


def _search_bing(query, max_results, language=None):
    query_text = urllib.parse.quote(query)
    url = f"https://cn.bing.com/search?q={query_text}&setlang={urllib.parse.quote(language or 'en-US')}"
    html, _ = _request_text(url)
    BeautifulSoup = get_bs4()
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for item in soup.select("li.b_algo"):
        if len(results) >= max_results * 2:
            break
        title_tag = item.select_one("h2 > a")
        if not title_tag:
            continue
        standardized = _standardize_search_result(
            {
                "title": title_tag.get_text(" ", strip=True),
                "href": title_tag.get("href"),
                "body": (item.select_one(".b_caption p") or {}).get_text(" ", strip=True)
                if item.select_one(".b_caption p")
                else "",
            },
            "bing",
        )
        if standardized:
            results.append(standardized)
    return results


def search_web(query, max_results=8, allowed_domains=None, blocked_domains=None, region=None, language=None):
    started_at = _now_ms()
    query = (query or "").strip()
    allowed_domains = _normalize_domains(allowed_domains)
    blocked_domains = _normalize_domains(blocked_domains)
    if not query:
        return _json_response(
            _make_error_payload(
                "query_required",
                query=query,
                provider_used="",
                fallback_chain=[],
                results=[],
                sources=[],
                warnings=[],
                duration_ms=_now_ms() - started_at,
            )
        )
    if len(query) < 2:
        return _json_response(
            _make_error_payload(
                "query_too_short",
                query=query,
                provider_used="",
                fallback_chain=[],
                results=[],
                sources=[],
                warnings=[],
                duration_ms=_now_ms() - started_at,
            )
        )
    if allowed_domains and blocked_domains:
        return _json_response(
            _make_error_payload(
                "allowed_and_blocked_domains_conflict",
                query=query,
                provider_used="",
                fallback_chain=[],
                results=[],
                sources=[],
                warnings=[],
                duration_ms=_now_ms() - started_at,
            )
        )

    cache_key = _build_cache_key(query, max_results, allowed_domains, blocked_domains, region, language)
    cached = _get_cache(_SEARCH_CACHE, cache_key, SEARCH_CACHE_TTL_SECONDS)
    if cached is not None:
        cached["warnings"] = list(cached.get("warnings") or [])
        cached["warnings"].append("cache_hit")
        return _json_response(cached)

    fallback_chain = []
    warnings = []
    provider_used = ""
    results = []
    search_errors = []
    providers = [
        ("duckduckgo", lambda: _search_duckduckgo(query, max_results, region=region)),
        ("bing", lambda: _search_bing(query, max_results, language=language)),
    ]

    for provider_name, provider_fn in providers:
        fallback_chain.append(provider_name)
        try:
            provider_results = provider_fn()
            provider_results = _apply_domain_filters(provider_results, allowed_domains, blocked_domains)
            provider_results = _dedupe_results(provider_results, max_results)
            if provider_results:
                provider_used = provider_name
                results = provider_results
                break
            search_errors.append(f"{provider_name}:no_results")
        except Exception as exc:
            search_errors.append(f"{provider_name}:{exc}")

    payload = {
        "ok": bool(results),
        "query": query,
        "provider_used": provider_used,
        "fallback_chain": fallback_chain,
        "results": results,
        "sources": [item["url"] for item in results],
        "warnings": warnings,
        "duration_ms": _now_ms() - started_at,
        "error": None if results else "; ".join(search_errors) or "search_failed",
    }
    _set_cache(_SEARCH_CACHE, cache_key, SEARCH_CACHE_TTL_SECONDS, payload)
    return _json_response(payload)


def read_web_article(url, max_chars=12000):
    started_at = _now_ms()
    normalized = _normalize_url(url)
    if not normalized:
        return _json_response(
            _make_article_result(
                url or "",
                error="empty_url",
                duration_ms=_now_ms() - started_at,
            )
        )

    cache_key = _build_cache_key(normalized, max_chars)
    cached = _get_cache(_ARTICLE_CACHE, cache_key, ARTICLE_CACHE_TTL_SECONDS)
    if cached is not None:
        cached["warnings"] = list(cached.get("warnings") or [])
        cached["warnings"].append("cache_hit")
        return _json_response(cached)

    warnings = []
    extractor_name, final_url, content, attempts = _fetch_article_via_proxies(normalized)
    if extractor_name:
        payload = _make_article_result(
            normalized,
            final_url=final_url,
            extractor_used=extractor_name,
            title=_extract_title(content),
            content=_truncate(content, max_chars),
            warnings=warnings,
            duration_ms=_now_ms() - started_at,
        )
        _set_cache(_ARTICLE_CACHE, cache_key, ARTICLE_CACHE_TTL_SECONDS, payload)
        return _json_response(payload)

    warnings.extend(
        f"{item['extractor']}:{item['reason']}"
        for item in attempts
        if not item.get("ok")
    )
    try:
        scrapling_text = _extract_with_scrapling(normalized)
        payload = _make_article_result(
            normalized,
            final_url=normalized,
            extractor_used="scrapling",
            title=_extract_title(scrapling_text),
            content=_truncate(scrapling_text, max_chars),
            warnings=warnings,
            duration_ms=_now_ms() - started_at,
        )
        _set_cache(_ARTICLE_CACHE, cache_key, ARTICLE_CACHE_TTL_SECONDS, payload)
        return _json_response(payload)
    except Exception as exc:
        payload = _make_article_result(
            normalized,
            final_url=normalized,
            extractor_used="",
            warnings=warnings,
            error=str(exc),
            duration_ms=_now_ms() - started_at,
        )
        return _json_response(payload)

