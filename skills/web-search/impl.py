import json
import requests
import urllib.parse
from urllib.parse import urlparse
from core.env_utils import ensure_package_installed

def get_bs4():
    ensure_package_installed("beautifulsoup4", "bs4")
    from bs4 import BeautifulSoup
    return BeautifulSoup

def get_ddgs():
    ensure_package_installed("duckduckgo-search", "duckduckgo_search")
    from duckduckgo_search import DDGS
    return DDGS

def get_trafilatura():
    ensure_package_installed("trafilatura")
    import trafilatura
    return trafilatura

def _normalize_url(url):
    u = (url or "").strip()
    if not u:
        return ""
    parsed = urlparse(u)
    if not parsed.scheme:
        u = "https://" + u
    return u

def _build_markdown_proxy_urls(url):
    normalized = _normalize_url(url)
    if not normalized:
        return []
    parsed = urlparse(normalized)
    host_path = parsed.netloc + parsed.path
    if parsed.query:
        host_path += "?" + parsed.query
    http_variant = "http://" + host_path
    candidates = [
        f"https://markdown.new/{normalized}",
        f"https://defuddle.md/{normalized}",
        f"https://r.jina.ai/{normalized}",
        f"https://r.jina.ai/{http_variant}"
    ]
    deduped = []
    for item in candidates:
        if item not in deduped:
            deduped.append(item)
    return deduped

def _fetch_text_direct(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code != 200:
        return None
    text = resp.text or ""
    cleaned = text.strip()
    if len(cleaned) < 80:
        return None
    return cleaned

def _extract_with_trafilatura(url):
    trafilatura = get_trafilatura()
    downloaded = trafilatura.fetch_url(url)
    if downloaded is None:
        return None
    text = trafilatura.extract(downloaded)
    return text.strip() if text else None

def _extract_with_scrapling(url):
    try:
        ensure_package_installed("scrapling")
    except Exception:
        return None
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
            for method_name in ["get", "fetch", "request"]:
                method = getattr(instance, method_name, None)
                if not callable(method):
                    continue
                result = method(url)
                text_candidates = [
                    getattr(result, "text", None),
                    getattr(result, "content", None),
                    str(result) if result is not None else None
                ]
                for txt in text_candidates:
                    if isinstance(txt, bytes):
                        try:
                            txt = txt.decode("utf-8", errors="replace")
                        except Exception:
                            txt = None
                    if isinstance(txt, str) and len(txt.strip()) > 120:
                        return txt.strip()
        except Exception:
            continue
    return None

def _search_bing_fallback(query, max_results=5):
    """
    Fallback search using Bing scraping.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        # Use cn.bing.com for better accessibility in China
        url = f"https://cn.bing.com/search?q={urllib.parse.quote(query)}"
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            return f"Error: Bing returned status {response.status_code}"
            
        BeautifulSoup = get_bs4()
        soup = BeautifulSoup(response.text, 'html.parser')
        results = []
        
        # Bing search results are usually in <li class="b_algo">
        for item in soup.select('li.b_algo'):
            if len(results) >= max_results:
                break
                
            title_tag = item.select_one('h2 > a')
            if not title_tag:
                continue
                
            link = title_tag.get('href')
            title = title_tag.get_text()
            
            snippet_tag = item.select_one('.b_caption p')
            snippet = snippet_tag.get_text() if snippet_tag else ""
            
            results.append({
                "title": title,
                "href": link,
                "body": snippet
            })
            
        return results
    except Exception as e:
        return []

def search_web(query, max_results=5):
    """
    Search the web using DuckDuckGo, falling back to Bing if needed.
    
    Args:
        query (str): The search query.
        max_results (int): Maximum number of results to return (default 5).
    """
    results = []
    
    # 1. Try DuckDuckGo
    try:
        DDGS = get_ddgs()
        with DDGS() as ddgs:
            # text() returns an iterator
            for r in ddgs.text(query, max_results=max_results):
                results.append(r)
    except Exception as e:
        # 2. Fallback to Bing
        print(f"DuckDuckGo failed ({str(e)}), trying Bing...")
        results = _search_bing_fallback(query, max_results)
        
    if not results:
         return "Error: No results found or search failed."
         
    return json.dumps(results, ensure_ascii=False)

def read_article(url):
    """
    Extract the main text content from a web page URL.
    
    Args:
        url (str): The URL of the article to read.
    """
    try:
        normalized = _normalize_url(url)
        if not normalized:
            return "Error: Empty URL."

        primary = _extract_with_trafilatura(normalized)
        if primary:
            return primary

        for proxy_url in _build_markdown_proxy_urls(normalized):
            proxied = _fetch_text_direct(proxy_url)
            if proxied:
                return proxied

            proxied_extracted = _extract_with_trafilatura(proxy_url)
            if proxied_extracted:
                return proxied_extracted

        scrapling_text = _extract_with_scrapling(normalized)
        if scrapling_text:
            return scrapling_text

        return "Error: Could not extract text with direct fetch, markdown proxies, or Scrapling fallback."
    except Exception as e:
        return f"Error reading article: {str(e)}"
