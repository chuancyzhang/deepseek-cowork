import json
import requests
import urllib.parse
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
        trafilatura = get_trafilatura()
        downloaded = trafilatura.fetch_url(url)
        if downloaded is None:
            return "Error: Could not fetch URL (404 or blocked)."
            
        text = trafilatura.extract(downloaded)
        if text is None:
            return "Error: Could not extract text content from the page."
            
        return text
    except Exception as e:
        return f"Error reading article: {str(e)}"
