import re
import html
import httpx
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

async def search_web(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Searches the live web via DuckDuckGo HTML and returns list of {title, url, snippet}."""
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    data = {"q": query}

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            res = await client.post(url, data=data, headers=headers)
            if res.status_code != 200:
                # Try GET fallback
                res = await client.get(url, params={"q": query}, headers=headers)

            if res.status_code != 200:
                return [{"title": "Search Error", "url": "", "snippet": f"Web search returned HTTP {res.status_code}"}]

            soup = BeautifulSoup(res.text, "html.parser")
            results = []

            # DuckDuckGo HTML layout
            items = soup.select(".web-result") or soup.select(".result")
            for item in items:
                title_elem = item.select_one(".result__title") or item.select_one("h2")
                snippet_elem = item.select_one(".result__snippet")
                url_elem = item.select_one(".result__url") or item.select_one("a.result__url")

                title = title_elem.get_text(strip=True) if title_elem else ""
                snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                href = ""
                if title_elem and title_elem.find("a"):
                    href = title_elem.find("a").get("href", "")
                elif url_elem:
                    href = url_elem.get("href", "")

                # Clean DuckDuckGo redirect link /uddg?uddg=...
                if "uddg=" in href:
                    match = re.search(r"uddg=([^&]+)", href)
                    if match:
                        import urllib.parse
                        href = urllib.parse.unquote(match.group(1))

                if title and snippet:
                    results.append({
                        "title": title,
                        "url": href,
                        "snippet": snippet
                    })

                if len(results) >= max_results:
                    break

            return results
        except Exception as e:
            return [{"title": "Search Error", "url": "", "snippet": f"Could not perform web search: {str(e)}"}]

async def scrape_webpage(url: str, max_chars: int = 5000) -> Dict[str, Any]:
    """Fetches and extracts clean readable text from a webpage."""
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        try:
            res = await client.get(url, headers=headers)
            if res.status_code != 200:
                return {"url": url, "error": f"Failed with status {res.status_code}"}

            soup = BeautifulSoup(res.text, "html.parser")

            # Remove scripts, styles, iframes, SVGs
            for tag in soup(["script", "style", "nav", "footer", "header", "svg", "noscript"]):
                tag.decompose()

            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            
            # Extract main content or body
            main = soup.find("main") or soup.find("article") or soup.body or soup
            text = main.get_text(separator="\n", strip=True)

            # Clean excess whitespace
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            clean_text = "\n".join(lines)[:max_chars]

            return {
                "url": url,
                "title": title,
                "content": clean_text,
                "length": len(clean_text)
            }
        except Exception as e:
            return {"url": url, "error": f"Scrape failed: {str(e)}"}
