"""
Reading the web with Xod's own browser: no API keys, no search service in between.

search(query)          searches like a person would (a search engine's results page in Xod's Chrome) and
                       returns [{title, url, snippet}].
read(urls, query)      opens the pages in parallel background tabs, waits for them to render (so JavaScript
                       sites work), and keeps the main text: the passages that best answer `query` first.
search_and_read(q)     both: the hits, with the top few pages' text attached as "content".

Everything runs in tabs that open in the background of Xod's browser and close when done, so a browser task
the user is watching is never disturbed. Engines that show a bot check are skipped for a while and the next one
is used; when Chrome can't start, the keyless HTTP search (web_search.search_web) is the fallback.
"""
import asyncio
import json
import logging
import math
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

import websockets

logger = logging.getLogger(__name__)

READ_TIMEOUT = 9.0          # per page: most sites are readable in 1-3 s; slow ones are cut off here
MAX_PARALLEL = 4
BLOCK_COOLDOWN = 30 * 60    # an engine that showed a bot check is left alone this long

# Sites that are never worth opening for an answer (logins, app stores, video pages with no text).
SKIP_HOST_RE = re.compile(r"(^|\.)(facebook|instagram|tiktok|pinterest|linkedin|x|twitter|youtube|spotify|soundcloud|twitch)\.com$|"
                          r"accounts\.google\.|(^|\.)(apps\.apple|play\.google)\.com$|^youtu\.be$", re.I)
SKIP_URL_RE = re.compile(r"\.(pdf|zip|dmg|exe|mp4|mp3|png|jpe?g|gif)(\?|$)", re.I)

# Reader tabs only need text. Skipping images, fonts, video and ad/tracking scripts makes pages load faster and,
# on a Mac short on memory, keeps Chrome from slowing the local model down while it works.
BLOCKED_URLS = [
    "*.png*", "*.jpg*", "*.jpeg*", "*.gif*", "*.webp*", "*.avif*", "*.svg*", "*.ico*", "*.bmp*",
    "*.woff*", "*.ttf*", "*.otf*", "*.eot*", "*.mp4*", "*.webm*", "*.m3u8*", "*.mp3*", "*.m4a*", "*.ogg*",
    "*doubleclick.net*", "*googlesyndication.com*", "*googletagmanager.com*", "*google-analytics.com*",
    "*googleadservices.com*", "*adservice.google.*", "*amazon-adsystem.com*", "*facebook.net*", "*connect.facebook.*",
    "*scorecardresearch.com*", "*taboola.com*", "*outbrain.com*", "*criteo.*", "*adnxs.com*", "*pubmatic.com*",
    "*rubiconproject.com*", "*hotjar.com*", "*segment.io*", "*segment.com/analytics*", "*optimizely.com*",
    "*chartbeat.*", "*quantserve.com*", "*moatads.com*", "*branch.io*", "*intercom.io*", "*cdn.onesignal.com*",
]

# Each engine: its results URL and a script that lists results (title, url, snippet) or reports a bot check.
ENGINES: Dict[str, Dict[str, str]] = {
    "google": {
        "url": "https://www.google.com/search?hl=en&num=10&q={q}",
        # Results are on the page: no need to wait for the rest (the AI overview keeps loading for seconds).
        "ready": "document.querySelectorAll('#rso a h3').length >= 5 || /\\/sorry\\//.test(location.pathname)",
        # Result links are redirect tokens (/goto?url=...): "open" keeps that link, and search() asks Google where each
        # one leads. Until then the site from the breadcrumb under the title stands in.
        "js": r"""(() => {
          if (/\/sorry\//.test(location.pathname) || document.querySelector('#captcha-form, form[action*="sorry"]')) return {blocked: true};
          const out = [], seen = new Set();
          for (const h of document.querySelectorAll('#rso a h3, #search a h3')) {
            const a = h.closest('a'); if (!a || !a.href) continue;
            let box = a;
            for (let i = 0; i < 8 && box.parentElement; i++) { box = box.parentElement; if (box.querySelector('cite') && box.innerText.length > h.innerText.length + 80) break; }
            const cite = [...box.querySelectorAll('cite')].map(c => c.innerText.trim()).find(Boolean) || '';
            let url = a.href;
            if (/google\.[a-z.]+\/(goto|url)\b/.test(url)) {
              const m = cite.match(/^(https?:\/\/[^\s›]+)/);
              if (!m) continue;
              url = m[1];
            }
            if (/^https?:\/\/(www\.)?google\./.test(url) || seen.has(url)) continue;
            seen.add(url);
            const sn = box.querySelector('.VwiC3b, [data-sncf], div[style*="-webkit-line-clamp"]');
            let snippet = sn ? sn.innerText.trim() : box.innerText.split('\n').map(s => s.trim())
              .filter(s => s.length > 40 && s !== h.innerText.trim() && !s.startsWith('http')).join(' ');
            out.push({title: h.innerText.trim(), url, open: a.href, snippet});
          }
          return {results: out};
        })()""",
    },
    "bing": {
        "url": "https://www.bing.com/search?setlang=en&cc=US&q={q}",
        "ready": "document.querySelectorAll('#b_results > li.b_algo').length >= 4",
        "js": r"""(() => {
          if (document.querySelector('#b_captcha, iframe[src*="challenge"]') || /\/challenge\//.test(location.pathname)) return {blocked: true};
          const real = (href) => {
            try { const u = new URL(href); const p = u.searchParams.get('u');
              if (u.hostname.endsWith('bing.com') && p && p.startsWith('a1')) {
                return atob(p.slice(2).replace(/-/g, '+').replace(/_/g, '/')); } } catch (e) {}
            return href;
          };
          const out = [];
          for (const li of document.querySelectorAll('#b_results > li.b_algo')) {
            const a = li.querySelector('h2 a'); if (!a) continue;
            const sn = li.querySelector('.b_caption p, .b_lineclamp2, .b_lineclamp3, .b_lineclamp4, .b_paractl');
            out.push({title: a.innerText.trim(), url: real(a.href), snippet: sn ? sn.innerText.trim() : ''});
          }
          return {results: out};
        })()""",
    },
    "duckduckgo": {
        "url": "https://html.duckduckgo.com/html/?q={q}",
        "ready": "document.querySelectorAll('.result').length >= 4 || !!document.querySelector('.anomaly-modal')",
        "js": r"""(() => {
          if (document.querySelector('.anomaly-modal, #challenge-form')) return {blocked: true};
          const out = [];
          for (const r of document.querySelectorAll('.result:not(.result--ad)')) {
            const a = r.querySelector('a.result__a'); if (!a) continue;
            let url = a.href;
            try { const u = new URL(url); if (u.searchParams.get('uddg')) url = u.searchParams.get('uddg'); } catch (e) {}
            const sn = r.querySelector('.result__snippet');
            out.push({title: a.innerText.trim(), url, snippet: sn ? sn.innerText.trim() : ''});
          }
          return {results: out};
        })()""",
    },
}
ENGINE_ORDER = ["google", "duckduckgo", "bing"]   # Bing gives thin results to a headless browser
_blocked_until: Dict[str, float] = {}
# Shared by every search and read, so several at once (a plan with three searches) stay at a few tabs total.
_tab_slots: Optional[asyncio.Semaphore] = None
_launch_lock: Optional[asyncio.Lock] = None
_last_engine: Dict[str, str] = {}

# The page's main text: drop menus, footers, cookie banners and the like, keep headings, paragraphs, lists
# and tables, roughly like a reader view.
READ_JS = r"""(() => {
  const junk = 'script,style,noscript,svg,canvas,iframe,nav,footer,header,aside,form,button,[role=navigation],[role=banner],' +
    '[role=contentinfo],[aria-hidden=true],[class*=cookie],[id*=cookie],[class*=consent],[class*=newsletter],[class*=subscribe],' +
    '[class*=share],[class*=related],[class*=sidebar],[class*=comment],[class*=advert],[class*=promo],[id*=onetrust],[class*=onetrust],' +
    '[id*=didomi],[class*=gdpr],[role=dialog],[aria-modal=true],figure,.navbox,.reflist,.mw-editsection,sup.reference,.hatnote';
  const pick = () => {
    const cands = [...document.querySelectorAll('article, main, [role=main], #content, .content, .post, .entry-content, #mw-content-text')];
    let best = null, bestLen = 0;
    for (const c of cands) { const n = (c.innerText || '').length; if (n > bestLen) { best = c; bestLen = n; } }
    return bestLen > 400 ? best : document.body;
  };
  const root = (pick() || document.body).cloneNode(true);
  root.querySelectorAll(junk).forEach(n => n.remove());
  const blocks = [];
  root.querySelectorAll('h1,h2,h3,h4,p,li,pre,blockquote,tr,dd,dt').forEach(el => {
    if (el.parentElement && el.parentElement.closest('li,tr') && !/^(LI|TR)$/.test(el.tagName)) return;  // part of its item or row
    let t = el.tagName === 'TR' ? [...el.children].map(c => (c.textContent || '').replace(/\s+/g, ' ').trim()).filter(Boolean).join(' | ')
                                : (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!t) return;
    if (/^H[1-4]$/.test(el.tagName)) t = '## ' + t;
    else if (el.tagName === 'LI') t = '- ' + t;
    blocks.push(t);
  });
  let text = blocks.join('\n');
  if (text.length < 300) text = (root.textContent || '').replace(/\s*\n\s*/g, '\n').replace(/[ \t]+/g, ' ');
  const meta = (n) => (document.querySelector(`meta[name="${n}"], meta[property="${n}"]`) || {}).content || '';
  return {title: document.title, url: location.href, text: text.slice(0, 60000),
          description: meta('description') || meta('og:description'),
          published: meta('article:published_time') || meta('date') || (document.querySelector('time[datetime]') || {}).dateTime || ''};
})()"""


def _port() -> int:
    from app.services.browser_sandbox import DEBUG_PORT
    return DEBUG_PORT


def _http_json(path: str, method: str = "GET") -> Any:
    req = urllib.request.Request(f"http://127.0.0.1:{_port()}{path}", method=method)
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read().decode())


class _Tab:
    """One background tab, driven over its own DevTools connection, closed on exit."""

    def __init__(self):
        self.id = ""
        self.ws = None
        self._n = 0

    async def __aenter__(self):
        global _tab_slots, _launch_lock
        from app.services.browser_sandbox import browser_sandbox_service as sb
        if _tab_slots is None:
            _tab_slots, _launch_lock = asyncio.Semaphore(MAX_PARALLEL), asyncio.Lock()
        await _tab_slots.acquire()
        self._slot = True
        try:
            async with _launch_lock:          # two first searches at once must not both start Chrome
                await sb.ensure_browser_running()
            return await self._open_tab(sb)
        except BaseException:
            await self.__aexit__(None, None, None)      # closes the tab if it got that far, frees the slot
            raise

    async def _open_tab(self, sb):
        browser_ws = (await asyncio.to_thread(_http_json, "/json/version"))["webSocketDebuggerUrl"]
        async with websockets.connect(browser_ws, max_size=8 * 1024 * 1024, ping_interval=None) as bws:
            await bws.send(json.dumps({"id": 1, "method": "Target.createTarget",
                                       "params": {"url": "about:blank", "background": True}}))
            while True:
                msg = json.loads(await asyncio.wait_for(bws.recv(), 5))
                if msg.get("id") == 1:
                    break
        if "error" in msg:
            raise RuntimeError(msg["error"].get("message", "couldn't open a tab"))
        self.id = msg["result"]["targetId"]
        sb.reader_tabs.add(self.id)
        self.ws = await websockets.connect(f"ws://127.0.0.1:{_port()}/devtools/page/{self.id}", max_size=32 * 1024 * 1024, ping_interval=None)
        from app.services.browser_sandbox import BOOTSTRAP_SCRIPT
        await self.cmd("Page.enable")
        try:
            await self.cmd("Page.addScriptToEvaluateOnNewDocument", {"source": BOOTSTRAP_SCRIPT})
        except Exception:
            pass
        try:
            await self.cmd("Network.enable")
            await self.cmd("Network.setBlockedURLs", {"urls": BLOCKED_URLS})
        except Exception:
            pass
        return self

    async def __aexit__(self, *exc):
        from app.services.browser_sandbox import browser_sandbox_service as sb
        try:
            if self.ws:
                await self.ws.close()
        except Exception:
            pass
        if self.id:
            try:
                await asyncio.to_thread(_http_json, f"/json/close/{self.id}")
            except Exception:
                pass
        sb.reader_tabs.discard(self.id)
        if getattr(self, "_slot", False) and _tab_slots is not None:
            _tab_slots.release()
            self._slot = False

    async def cmd(self, method: str, params: Optional[Dict[str, Any]] = None, timeout: float = 10.0) -> Dict[str, Any]:
        self._n += 1
        n = self._n
        await self.ws.send(json.dumps({"id": n, "method": method, "params": params or {}}))
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError(method)
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), left))
            if msg.get("id") == n:
                if "error" in msg:
                    raise RuntimeError(msg["error"].get("message", method))
                return msg.get("result", {})

    async def js(self, expr: str, timeout: float = 8.0) -> Any:
        res = await self.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True}, timeout)
        return (res.get("result") or {}).get("value")

    async def open(self, url: str, timeout: float = READ_TIMEOUT, min_text: int = 200, ready: str = "") -> None:
        """Goes to the page and waits until it has loaded and shows text (sites that render with JavaScript
        included), or until the time is up: whatever is there by then is read. `ready`: a check that says the
        part we need is already there, so there's no need to wait for the rest."""
        await self.cmd("Page.navigate", {"url": url}, timeout=timeout)
        end = time.monotonic() + timeout
        last, steady = -1, 0
        while time.monotonic() < end:
            await asyncio.sleep(0.15 if ready else 0.25)
            if ready:
                try:
                    if await self.js(f"(() => {{ try {{ return !!({ready}); }} catch (e) {{ return false; }} }})()", 3):
                        return
                except Exception:
                    pass
            try:
                st = await self.js("({s: document.readyState, n: (document.body && document.body.innerText || '').length})", 3)
            except Exception:
                continue
            st = st or {}
            n = st.get("n", 0)
            steady = steady + 1 if n == last else 0
            last = n
            if st.get("s") in ("interactive", "complete") and n >= min_text and steady >= 2:
                return
            if st.get("s") == "complete" and steady >= 6:      # loaded, little text, not changing
                return


# ------------------------------------------------------------------------------------------- search

async def search(query: str, max_results: int = 8) -> Dict[str, Any]:
    """Results from a search engine's own page, in Xod's browser. Returns {results, engine} or {results: [], error}."""
    q = urllib.parse.quote_plus(query)
    tried = []
    for name in ENGINE_ORDER:
        if _blocked_until.get(name, 0) > time.time():
            continue
        tried.append(name)
        eng = ENGINES[name]
        try:
            async with _Tab() as tab:
                await tab.open(eng["url"].format(q=q), timeout=8, min_text=300, ready=eng.get("ready", ""))
                got = await tab.js(eng["js"]) or {}
        except Exception as e:
            logger.info(f"web_reader: {name} search failed: {e}")
            continue
        if got.get("blocked"):
            _blocked_until[name] = time.time() + BLOCK_COOLDOWN
            logger.info(f"web_reader: {name} showed a bot check; using another engine for a while")
            continue
        hits, seen = [], set()
        for h in got.get("results") or []:
            url = str(h.get("url") or "")
            host = urllib.parse.urlparse(url).hostname or ""
            if not url.startswith("http") or url in seen or not h.get("title"):
                continue
            seen.add(url)
            hit = {"title": h["title"][:200], "url": url, "snippet": str(h.get("snippet") or "")[:400], "host": host}
            if h.get("open") and h["open"] != url:
                hit["open"] = h["open"]
            hits.append(hit)
            if len(hits) >= max_results:
                break
        if hits:
            _last_engine["name"] = name
            await _resolve_redirects(hits)
            return {"results": hits, "engine": name}
    # Chrome couldn't search (not installed, every engine blocked): the keyless HTTP search.
    try:
        from app.services.web_search import search_web
        hits = [h for h in await search_web(query, max_results) if h.get("url")]
        if hits:
            return {"results": hits, "engine": "http"}
    except Exception:
        pass
    return {"results": [], "error": f"no search engine answered (tried {', '.join(tried) or 'none'})"}


async def _resolve_redirects(hits: List[Dict[str, Any]]) -> None:
    """Google's result links go through its own redirect. Asking Google (not the site) where each one leads gives
    the exact page address for citations, all at once, in well under a second."""
    todo = [h for h in hits if h.get("open")]
    if not todo:
        return
    import httpx
    from app.services.web_search import DEFAULT_USER_AGENT

    async def one(client, h):
        try:
            r = await client.get(h["open"])
            loc = r.headers.get("location") or ""
            if r.status_code in (301, 302, 303, 307, 308) and loc.startswith("http") and "google." not in urllib.parse.urlparse(loc).netloc:
                h["url"] = loc
                h["host"] = urllib.parse.urlparse(loc).hostname or h.get("host", "")
                h["open"] = loc
        except Exception:
            pass
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=3.0, headers={"User-Agent": DEFAULT_USER_AGENT}) as client:
            await asyncio.gather(*(one(client, h) for h in todo))
    except Exception:
        pass


# --------------------------------------------------------------------------------------------- read

_WORD = re.compile(r"[a-z0-9][a-z0-9'.-]*", re.I)
_STOP = set("the a an and or of to in on for with is are was were be by at from as it its this that what which who how "
            "when where why do does did i you me my your we our can will should would about into than then there their".split())


def _terms(s: str) -> List[str]:
    return [w.lower().strip(".'") for w in _WORD.findall(s or "") if w.lower() not in _STOP and len(w) > 1]


_BOILER_RE = re.compile(r"\b(cookies?|consent|privacy (policy|settings)|subscribe|sign up for|newsletter|advertis|javascript (is )?"
                        r"(disabled|required)|accept all|all rights reserved)\b", re.I)


def best_passages(text: str, query: str, budget: int = 1500) -> str:
    """The parts of a page that answer the query: the opening paragraph (pages lead with their point) plus the
    paragraphs that share the most of the query's words (rarer words count more), in page order."""
    paras = [p.strip() for p in re.split(r"\n+", text or "") if len(p.strip()) > 30]
    paras = [p for p in paras if not (_BOILER_RE.search(p) and len(_BOILER_RE.findall(p)) * 60 > len(p) / 4)]
    if not paras:
        return (text or "")[:budget]
    q = set(_terms(query))
    df = {t: sum(1 for p in paras if t in p.lower()) for t in q}
    n = len(paras)
    lead = next((i for i, p in enumerate(paras) if len(p) >= 120 and not p.startswith(("## ", "- ")) and "|" not in p), 0)

    def score(i: int) -> float:
        p = paras[i]
        low = p.lower()
        s = sum(math.log(1 + n / (1 + df[t])) for t in q if t in low)
        if re.search(r"\d", p):
            s *= 1.15         # numbers, dates and prices are usually what's being asked
        if i < lead + 3:
            s *= 1.3          # near the top: summaries and answers
        return s / (1 + len(p) / 900)

    keep, used = {lead}, min(len(paras[lead]), 700)
    for i in sorted(range(n), key=lambda i: -score(i)):
        if used >= budget or score(i) <= 0:
            break
        if i not in keep:
            keep.add(i)
            used += min(len(paras[i]), 700)
    out, prev = [], -1
    for i in sorted(keep):
        if out and i - prev > 1:
            out.append("…")
        out.append(paras[i] if len(paras[i]) <= 700 else paras[i][:700] + "…")
        prev = i
    return "\n".join(out)[: budget + 300]


async def read_page(url: str, query: str = "", budget: int = 1500, timeout: float = READ_TIMEOUT) -> Dict[str, Any]:
    t0 = time.monotonic()
    try:
        async with _Tab() as tab:
            await tab.open(url, timeout=timeout)
            page = await tab.js(READ_JS, 6) or {}
    except Exception as e:
        return {"url": url, "ok": False, "error": str(e)[:160]}
    text = str(page.get("text") or "")
    if len(text) < 150:
        return {"url": url, "ok": False, "error": "no readable text", "title": page.get("title", "")}
    return {"url": page.get("url") or url, "title": page.get("title", ""), "ok": True, "chars": len(text),
            "published": page.get("published", ""), "description": page.get("description", ""),
            "content": best_passages(text, query, budget) if query else text[:budget],
            "secs": round(time.monotonic() - t0, 1)}


async def read(urls: List[str], query: str = "", budget: int = 1500) -> List[Dict[str, Any]]:
    """Several pages at once (a few tabs at a time)."""
    sem = asyncio.Semaphore(MAX_PARALLEL)

    async def one(u):
        async with sem:
            try:
                return await asyncio.wait_for(read_page(u, query, budget), READ_TIMEOUT * 2 + 4)
            except asyncio.TimeoutError:
                return {"url": u, "ok": False, "error": "timed out"}
    return await asyncio.gather(*(one(u) for u in urls))


async def read_stream(urls: List[str], query: str = "", budget: int = 1500, settle: float = 1.2, timeout: float = 7.0,
                      futures: Optional[List[asyncio.Future]] = None):
    """Reads the pages in parallel and yields (index, page) as each one finishes, so the answer can start on the
    first pages while others load. Doesn't wait on stragglers: once two pages are in, the rest get `settle`
    seconds more; nothing waits past `timeout`. Unfinished reads are stopped (their tabs closed).
    `futures`: reads already started (one per url), e.g. by a search that ran ahead."""
    if futures is not None and len(futures) == len(urls):
        tasks = {f: i for i, f in enumerate(futures)}
    else:
        tasks = {asyncio.ensure_future(read_page(u, query, budget, timeout=min(READ_TIMEOUT, timeout))): i for i, u in enumerate(urls)}
    start, got, cutoff = time.monotonic(), 0, None
    try:
        pending = set(tasks)
        while pending:
            limit = start + timeout
            if cutoff is not None:
                limit = min(limit, cutoff)
            left = limit - time.monotonic()
            if left <= 0:
                break
            done, pending = await asyncio.wait(pending, timeout=left, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                try:
                    page = t.result()
                except Exception as e:
                    page = {"ok": False, "error": str(e)[:120]}
                if page.get("ok"):
                    got += 1
                    if got >= 2 and cutoff is None:
                        cutoff = time.monotonic() + settle
                yield tasks[t], page
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()


async def warm() -> bool:
    """Starts Xod's browser ahead of time (when you start typing), so the first search doesn't wait ~1-2 s for it."""
    from app.services.browser_sandbox import browser_sandbox_service as sb
    global _launch_lock
    if _launch_lock is None:
        _launch_lock = asyncio.Lock()
    try:
        async with _launch_lock:
            return bool(await sb.ensure_browser_running())
    except Exception:
        return False


def _readable(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return bool(host) and not SKIP_HOST_RE.search(host) and not SKIP_URL_RE.search(url)


def pick_pages(hits: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    """The results worth opening: in rank order, readable, one page per site (for variety)."""
    picks, hosts = [], set()
    for h in hits:
        host = h.get("host") or urllib.parse.urlparse(h.get("url", "")).hostname
        if _readable(h.get("url", "")) and host not in hosts:
            picks.append(h)
            hosts.add(host)
        if len(picks) >= n:
            break
    return picks


async def search_and_read(query: str, read_top: int = 3, max_results: int = 8, budget: int = 1500) -> Dict[str, Any]:
    """Search, then read the top few readable results in parallel. The hits keep their order; the ones that were
    read carry the passages that answer the query in "content"."""
    t0 = time.monotonic()
    found = await search(query, max_results)
    hits = found.get("results") or []
    if not hits:
        return {"results": [], "provider": "browser", "error": found.get("error", "no results")}
    picks = pick_pages(hits, read_top)
    pages = await read([h.get("open") or h["url"] for h in picks], query, budget) if read_top else []
    for h, p in zip(picks, pages):
        if p.get("ok"):
            h["content"] = p["content"]
            if str(p.get("url", "")).startswith("http"):
                h["url"] = p["url"]           # where the link really led
            if p.get("published"):
                h["published"] = p["published"][:10]
    for h in hits:
        h.pop("open", None)
    return {"results": hits, "provider": "browser", "engine": found.get("engine"),
            "pages_read": sum(1 for p in pages if p.get("ok")), "secs": round(time.monotonic() - t0, 1)}
