"""
Shareable reports: turns an answer into a polished, self-contained web page (and a PDF) with a
numbered list of sources, ready to send to someone.

The page is one .html file with its fonts embedded, so it looks the same anywhere and works
offline. It carries a strict Content-Security-Policy (no scripts at all), and the HTML is cleaned
again here even though the app already sanitized it, so a report can't run anything when opened.
PDFs are printed by headless Chrome (which Xod already uses for its browser).

Files go to <data>/reports/ and are served at /reports/<name> on Xod's local server.
"""
import base64
import datetime as dt
import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.paths import APP_ROOT, DATA_DIR
from app.timefmt import nopad

REPORTS_DIR = DATA_DIR / "reports"
FONTS = APP_ROOT / "app" / "static" / "vendor" / "fonts"

_BAD_BLOCKS = re.compile(r"<(script|style|iframe|object|embed|form|link|meta|base)\b.*?(</\1>|/?>)", re.I | re.S)
_EVENT_ATTR = re.compile(r"\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.I)
_JS_URL = re.compile(r"(href|src)\s*=\s*([\"'])\s*(javascript|vbscript|data:text/html)[^\"']*\2", re.I)
_LINK = re.compile(r"<a\b[^>]*href=([\"'])(https?://[^\"']+)\1[^>]*>(.*?)</a>", re.I | re.S)
_BUTTONS = re.compile(r"<span[^>]*data-reveal=[^>]*>.*?</span>\s*</span>", re.I | re.S)


def clean(fragment: str) -> str:
    """Defence in depth on top of DOMPurify: drop active content and Xod-only UI bits."""
    s = _BAD_BLOCKS.sub("", fragment or "")
    s = _EVENT_ATTR.sub("", s)
    s = _JS_URL.sub(r'\1="#"', s)
    s = _BUTTONS.sub("", s)            # "Open in Finder" buttons mean nothing to the reader
    s = re.sub(r'\sclass="[^"]*"', "", s)
    return s


def collect_sources(fragment: str, extra: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
    seen, out = set(), []
    for item in (extra or []):
        url = (item.get("url") or "").strip()
        if url.startswith("http") and url not in seen:
            seen.add(url); out.append({"url": url, "title": (item.get("title") or "").strip()})
    for _, url, text in _LINK.findall(fragment or ""):
        url = html.unescape(url)
        if url not in seen:
            seen.add(url)
            out.append({"url": url, "title": re.sub(r"<[^>]+>", "", text).strip()})
    for s in out:
        if not s["title"] or s["title"] == s["url"]:
            s["title"] = urlparse(s["url"]).netloc.replace("www.", "")
    return out[:60]


def _font_face(family: str, file_glob: str, style: str = "normal") -> str:
    f = next(iter(sorted(FONTS.glob(file_glob))), None)
    if not f:
        return ""
    data = base64.b64encode(f.read_bytes()).decode()
    weight = "300 800" if family == "Fraunces" else "400 700"
    return (f"@font-face{{font-family:'{family}';font-style:{style};font-weight:{weight};"
            f"src:url(data:font/woff2;base64,{data}) format('woff2');}}")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "report").lower()).strip("-")
    return s[:60] or "report"


def build_html(title: str, question: str, body_html: str, sources: List[Dict[str, str]], when: dt.datetime) -> str:
    fonts = _font_face("Fraunces", "fraunces-normal-*.woff2") + _font_face("Figtree", "figtree-normal-*.woff2")
    src_html = "".join(
        f'<li><a href="{html.escape(s["url"])}">{html.escape(s["title"])}</a>'
        f'<span class="dom">{html.escape(urlparse(s["url"]).netloc.replace("www.", ""))}</span></li>' for s in sources)
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src https: http: data:; style-src 'unsafe-inline'; font-src data:">
<meta name="referrer" content="no-referrer">
<title>{html.escape(title)}</title>
<style>
{fonts}
:root {{ --ink:#1d1a16; --muted:#6f685e; --rule:#e7e0d4; --accent:#b8761a; --bg:#fbf8f2; --card:#fff; }}
@media (prefers-color-scheme: dark) {{ :root {{ --ink:#f3ecdf; --muted:#a39e97; --rule:#2a2e47; --accent:#f2b24a; --bg:#0f1222; --card:#13172a; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.65 Figtree, -apple-system, system-ui, sans-serif; }}
main {{ max-width:760px; margin:0 auto; padding:56px 24px 72px; }}
.kicker {{ font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--accent); font-weight:600; }}
h1 {{ font-family:Fraunces, Georgia, serif; font-weight:600; font-size:clamp(30px,5vw,44px); line-height:1.12; margin:10px 0 14px; }}
.meta {{ color:var(--muted); font-size:14px; border-bottom:1px solid var(--rule); padding-bottom:22px; margin-bottom:28px; }}
.question {{ background:var(--card); border:1px solid var(--rule); border-radius:14px; padding:14px 18px; margin:0 0 30px; color:var(--muted); }}
.question b {{ color:var(--ink); }}
article h1, article h2, article h3 {{ font-family:Fraunces, Georgia, serif; line-height:1.25; }}
article h2 {{ font-size:24px; margin:34px 0 10px; }} article h3 {{ font-size:19px; margin:26px 0 8px; }}
article a, .sources a {{ color:var(--accent); text-decoration:underline; text-underline-offset:2px; }}
article table {{ border-collapse:collapse; width:100%; margin:18px 0; font-size:14px; display:block; overflow-x:auto; }}
article th, article td {{ border:1px solid var(--rule); padding:8px 10px; text-align:left; vertical-align:top; }}
article th {{ background:var(--card); }}
article img {{ max-width:100%; border-radius:12px; }}
article code {{ background:var(--card); border:1px solid var(--rule); border-radius:6px; padding:1px 5px; font-size:.9em; }}
article pre {{ background:var(--card); border:1px solid var(--rule); border-radius:12px; padding:14px; overflow-x:auto; }}
article blockquote {{ border-left:3px solid var(--accent); margin:18px 0; padding:2px 16px; color:var(--muted); }}
.sources {{ margin-top:44px; border-top:1px solid var(--rule); padding-top:22px; }}
.sources h2 {{ font-family:Fraunces, Georgia, serif; font-size:20px; margin:0 0 10px; }}
.sources ol {{ padding-left:22px; margin:0; }} .sources li {{ margin:6px 0; font-size:14px; }}
.dom {{ color:var(--muted); margin-left:8px; font-size:12px; }}
footer {{ margin-top:48px; color:var(--muted); font-size:12px; }}
@media print {{ body {{ background:#fff; color:#000; }} main {{ padding:0; max-width:none; }} a {{ color:#000; }} }}
</style></head>
<body><main>
<div class="kicker">Research report</div>
<h1>{html.escape(title)}</h1>
<div class="meta">{when.strftime(nopad("%B %-d, %Y"))} · {len(sources)} source{"s" if len(sources) != 1 else ""}</div>
{f'<div class="question"><b>Question:</b> {html.escape(question)}</div>' if question else ""}
<article>{body_html}</article>
{f'<section class="sources"><h2>Sources</h2><ol>{src_html}</ol></section>' if sources else ""}
<footer>Researched with Xod, a private AI that runs on a Mac.</footer>
</main></body></html>"""


def _chrome() -> Optional[str]:
    from app.services.browser_sandbox import CHROME_BINARIES
    return next((c for c in CHROME_BINARIES if c and Path(c).exists()), None)


def print_pdf(html_path: Path) -> Optional[Path]:
    """Headless Chrome with a throwaway profile. --use-mock-keychain: a fresh profile would otherwise
    wait on the macOS Keychain. Chrome doesn't always exit after printing, so wait for the file
    to appear and stop growing, then close it."""
    import time
    chrome = _chrome()
    if not chrome:
        return None
    pdf = html_path.with_suffix(".pdf")
    profile = tempfile.mkdtemp(prefix="xod-pdf-")
    proc = None
    try:
        proc = subprocess.Popen([chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                                 "--use-mock-keychain", "--password-store=basic", "--disable-extensions",
                                 "--disable-background-networking", f"--user-data-dir={profile}", "--no-pdf-header-footer",
                                 f"--print-to-pdf={pdf}", html_path.as_uri()],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        last, stable = -1, 0
        deadline = time.time() + 60
        while time.time() < deadline:
            size = pdf.stat().st_size if pdf.exists() else 0
            stable = stable + 1 if size and size == last else 0
            if stable >= 3 or proc.poll() is not None and size:
                break
            last = size
            time.sleep(0.5)
    except Exception:
        return None
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except Exception:
                proc.kill()
        shutil.rmtree(profile, ignore_errors=True)
    return pdf if pdf.exists() and pdf.stat().st_size > 1000 else None


def make_report(title: str, question: str, body_html: str, sources: Optional[List[Dict[str, str]]] = None,
                pdf: bool = True) -> Dict[str, Any]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    when = dt.datetime.now()
    title = (title or question or "Research report").strip()[:140]
    body = clean(body_html)
    srcs = collect_sources(body_html, sources)
    name = f"{when.strftime('%Y-%m-%d')}-{_slug(title)}"
    path = REPORTS_DIR / f"{name}.html"
    n = 2
    while path.exists():
        path = REPORTS_DIR / f"{name}-{n}.html"; n += 1
    path.write_text(build_html(title, (question or "").strip()[:600], body, srcs, when), encoding="utf-8")
    pdf_path = print_pdf(path) if pdf else None
    return {"success": True, "title": title, "sources": len(srcs),
            "html_path": str(path), "html_url": f"/reports/{path.name}",
            "pdf_path": str(pdf_path) if pdf_path else None,
            "pdf_url": f"/reports/{pdf_path.name}" if pdf_path else None}


def make_report_from_markdown(title: str, markdown_text: str, question: str = "") -> Dict[str, Any]:
    """For the agent (tasks, routines): Markdown in, page + PDF out."""
    import markdown
    body = markdown.markdown(markdown_text or "", extensions=["tables", "fenced_code", "sane_lists"])
    return make_report(title, question, body)


def short_title(text: str, limit: int = 80) -> str:
    """Fallback title: first sentence if it fits, else cut at a word boundary."""
    t = " ".join((text or "").split())
    first = re.split(r"(?<=[.?!])\s", t, maxsplit=1)[0]
    if len(first) <= limit:
        return first.rstrip(".")
    return t[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


async def better_title(title: str, question: str) -> str:
    """Long titles are usually the raw question: ask the model for a few words (short timeout)."""
    import asyncio
    base = (title or question or "Research report").strip()
    if len(base) <= 60:
        return base
    try:
        from app.services.llm import llm_service
        msg = await asyncio.wait_for(llm_service.chat_completion(
            [{"role": "user", "content": "Write a 3 to 7 word title for a report answering this question. "
                                         f"Reply with the title only, no quotes.\n\n{base[:600]}"}],
            max_tokens=24, think=False, temperature=0.2), 20)
        t = re.sub(r"[\"*#]", "", (msg.get("content") or "").strip().splitlines()[0]).strip()
        if 3 <= len(t) <= 80:
            return t
    except Exception:
        pass
    return short_title(base)
