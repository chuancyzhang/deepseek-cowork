import os
import sys
import importlib.util

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load module dynamically
spec = importlib.util.spec_from_file_location("impl", os.path.join(os.path.dirname(__file__), '../skills/web-search/impl.py'))
impl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(impl)

def test_web_skill():
    print("Testing Web Search Skill...")
    
    # 1. Search
    print("\n1. Searching for 'DeepSeek'...")
    results = impl.search_web("DeepSeek", max_results=3)
    print(f"Search Results (First 100 chars): {results[:100]}...")
    
    # Check if results look like JSON list
    if results.startswith("[") and "DeepSeek" in results:
        print("Search test PASSED")
    else:
        print("Search test FAILED or Network Issue")

    # 2. Read Article (using a stable URL, e.g., example.com)
    # Note: example.com is very sparse, let's try python.org about page
    url = "https://www.python.org/about/"
    print(f"\n2. Reading article from {url}...")
    content = impl.read_article(url)
    print(f"Content length: {len(content)}")
    print(f"Content snippet: {content[:100].replace('\n', ' ')}")
    
    if "Python" in content and len(content) > 100:
        print("Read Article test PASSED")
    else:
        print("Read Article test FAILED or Network Issue")

if __name__ == "__main__":
    test_web_skill()
