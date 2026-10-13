"""
Reading the web in Xod's own browser (services/web_reader.py), against a pretend search engine and pretend
sites served on this Mac, so it runs offline: results are parsed from the engine's page, the top pages are
read in parallel background tabs (a page that renders with JavaScript included, cookie banners left out),
the passages that answer the question come first, tabs are closed afterwards, and none of it touches the tab
a browser task is driving. Also checks the mission helpers (compact element list, link-free page text,
chained actions finding their element after the page shifts).

Throwaway data folder and browser profile, no model needed:
  .venv/bin/python tests/smoke/run_web_reader_test.py
"""
import asyncio
import os
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["XOD_DATA_DIR"] = tempfile.mkdtemp(prefix="xod-reader-")

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
SPA = f"http://spa.localhost:{PORT}"       # separate sites (the reader takes one page per site)
SLOW = f"http://slow.localhost:{PORT}"
FILLER = "<p>" + "Glaciers have shaped valleys for millions of years and carry rocks for long distances. " * 4 + "</p>"
PAGES = {
    # A search engine results page in DuckDuckGo's HTML layout (one ad, three results).
    "/html/": f"""<html><body>
      <div class="result result--ad"><a class="result__a" href="https://ads.example/x">Buy glaciers</a><a class="result__snippet">ad</a></div>
      <div class="result"><a class="result__a" href="{BASE}/article">How fast do glaciers move?</a><a class="result__snippet">Speeds vary a lot.</a></div>
      <div class="result"><a class="result__a" href="{SPA}/spa">Glacier speed tracker</a><a class="result__snippet">Live numbers.</a></div>
      <div class="result"><a class="result__a" href="{SLOW}/slow">A very slow page</a><a class="result__snippet">Takes forever.</a></div>
      <div class="result"><a class="result__a" href="https://www.facebook.com/glaciers">Glaciers on Facebook</a><a class="result__snippet">Social.</a></div>
    </body></html>""",
    "/blocked": """<html><body><div class="anomaly-modal">Please confirm you're human</div></body></html>""",
    "/article": f"""<html><head><title>How fast do glaciers move?</title><meta name="description" content="Glacier speeds explained">
      <meta property="article:published_time" content="2026-09-30T10:00:00Z"></head><body>
      <nav><a href="/">Home</a> <a href="/about">About us and our cookie policy</a></nav>
      <div id="onetrust-banner-sdk"><p>We use cookies to improve your experience. Accept all cookies or manage your privacy settings.</p></div>
      <article><h1>How fast do glaciers move?</h1>
      <p>Most glaciers move slowly, usually between a few centimetres and a few metres per day, depending on slope and ice thickness.</p>
      {FILLER}{FILLER}{FILLER}
      <p>The fastest glacier on record, Jakobshavn Isbrae in Greenland, moved about 46 metres per day in the summer of 2012.</p>
      {FILLER}{FILLER}</article>
      <footer>Copyright. Subscribe to our newsletter.</footer></body></html>""",
    # Renders its text with JavaScript after a moment, like a single-page app.
    "/spa": """<html><head><title>Glacier speed tracker</title></head><body><div id="app">Loading…</div><script>
      setTimeout(() => { document.getElementById('app').innerHTML = '<main><h1>Tracker</h1><p>Live glacier speed today: the Kangerlussuaq '
        + 'glacier is moving 31 metres per day, measured by satellite radar this morning.</p><p>' + 'More background about how radar measures ice. '.repeat(12) + '</p></main>'; }, 700);
    </script></body></html>""",
}


class Site(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/slow":
            time.sleep(30)
        body = PAGES.get(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        try:
            self.wfile.write(body.encode())
        except Exception:
            pass


def helpers():
    from app.services import browser_sandbox as B
    els = [{"index": 1, "tag": "input", "type": "text", "text": "(unlabeled)", "label": "Full name", "value": "Ana", "is_in_viewport": True},
           {"index": 2, "tag": "input", "type": "checkbox", "text": "Remember me", "label": "", "is_checked": True, "is_in_viewport": True},
           {"index": 3, "tag": "button", "type": "submit", "text": "Continue", "label": "", "is_in_viewport": False, "x": 10, "y": 900}]
    m = B._compact_element_map(els)
    check("element list: one short line each, real labels, no coordinates",
          '[1] input:text "(unlabeled)" (Full name) val="Ana"' in m and "[x]" in m and "[below]" in m and "900" not in m, m)
    md = "See [the docs](https://example.com/a/very/long/path?with=query) and ![logo](https://x/y.png) here."
    check("page text: link addresses and images dropped, link words kept", B._strip_link_targets(md) == "See the docs and  here.",
          B._strip_link_targets(md))
    sig = B._element_sig(els[2])
    shifted = [{**e, "index": e["index"] + 1} for e in els]          # a notice appeared above: every number moved
    check("a chained action finds its element after the numbers shift", (B._match_element(shifted, sig, 3) or {}).get("index") == 4)
    check("…and gives up when the element is gone", B._match_element(els[:2], sig, 3) is None)


async def main():
    helpers()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from app.services import web_reader as W
    from app.services.browser_sandbox import browser_sandbox_service as sb
    W.ENGINES["duckduckgo"]["url"] = BASE + "/html/?q={q}"
    W.ENGINE_ORDER[:] = ["duckduckgo"]
    W.READ_TIMEOUT = 5.0

    # The tab a browser task is driving.
    await sb.navigate(BASE + "/article")
    driven = sb.active_tab_id

    t = time.time()
    found = await W.search("how fast do glaciers move")
    urls = [h["url"] for h in found.get("results", [])]
    check("search: results come from the engine's page, ads left out", found.get("engine") == "duckduckgo" and len(urls) == 4
          and not any("ads.example" in u for u in urls), str(found)[:300])

    r = await W.search_and_read("how fast do glaciers move", read_top=3)
    secs = time.time() - t
    by = {h["url"]: h for h in r.get("results", [])}
    art = by.get(BASE + "/article", {}).get("content", "")
    spa = by.get(SPA + "/spa", {}).get("content", "")
    check("the top pages are read, in parallel, and the slow one doesn't hold things up", r.get("pages_read") == 2 and secs < 14,
          f"read {r.get('pages_read')} in {secs:.1f}s")
    check("…the passage that answers comes through (and the opening paragraph)", "46 metres per day" in art and "few centimetres" in art, art[:400])
    check("…without the cookie banner, menu or footer", "cookies" not in art.lower() and "newsletter" not in art.lower(), art[:400])
    check("…with the page's date", by.get(BASE + "/article", {}).get("published") == "2026-09-30")
    check("a page that renders with JavaScript is read once it has rendered", "31 metres per day" in spa, spa[:200])
    check("social sites aren't opened", "content" not in by.get("https://www.facebook.com/glaciers", {}))

    await asyncio.sleep(0.5)
    tabs = (await sb.manage_tabs("list")).get("tabs", [])
    check("reader tabs are closed afterwards", not sb.reader_tabs and len([x for x in tabs if x.get("type") == "page"]) == 1, str(tabs))
    check("the driven tab is untouched", sb.active_tab_id == driven and "glaciers" in (await sb.evaluate_js("document.title")).get("result", "").lower())

    # An engine that shows a bot check is skipped for a while.
    W.ENGINES["duckduckgo"]["url"] = BASE + "/blocked?q={q}"
    W._blocked_until.clear()
    r = await W.search("glaciers")
    check("an engine with a bot check is set aside (not fought)", W._blocked_until.get("duckduckgo", 0) > time.time())
    srv.shutdown()

    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sb.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
