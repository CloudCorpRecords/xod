"""
browser runner, public web (AGE-100): read-only multi-tab runs against real
public sites through Xod's own sandbox browser. Every step is verified by
URL and content read-back by the runner itself; this suite adds run-level
checks and prints the full per-step record (landings, recoveries, failures
with causes) for the report.

Runs:
  P1 docs.python.org + en.wikipedia.org in two tabs: navigate, extract,
     switch tabs by URL, follow the tutorial link, close a tab, land back.
  P2 en.wikipedia.org search results (a GET page, no form submission):
     navigate to the results URL, follow the first result into an article.
  P3 MDN Web/HTTP (a client-rendered docs app): navigate, wait for the
     heading, extract, verify the overview text.
  P4 long page: en.wikipedia.org History of the Internet, scroll steps and
     a scroll to the page footer.
  PROBE (not gated): one navigation at a major news homepage. Bot walls are
     expected on some big sites; the probe names the wall instead of
     defeating it, per the sandbox's own doctrine.

Public GET-style browsing only: no accounts, no sign-ins, no form
submissions, no purchases.

NOTE: never redirect HOME for a process that launches Chrome (round 2
defect B). Isolation comes from XOD_DATA_DIR and the file keyring.

Run: python tests/smoke/run_browser_public_web_test.py
"""
import asyncio, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_public_web_"))
os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "XOD_NO_UPDATE_CHECK": "1", "XOD_SPEECH": "off",
                   "XDG_CONFIG_HOME": str(TMP / "config"),
                   "XDG_DATA_HOME": str(TMP / "share"),
                   "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_SECRETS_SERVICE": "Xod test keys"})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

from app import paths
paths.DEV_ENV_PATH = TMP / "absent.env"

results = []

def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  {detail}" if detail and not cond else ""))


def show_run(label, record):
    print(f"\n== {label}: status={record.get('status')} "
          f"landed={record.get('steps_landed')}/{record.get('steps_total')} "
          f"secs={record.get('secs')} final={record.get('final_url')}")
    for s in record.get("steps", []):
        strategies = ",".join(a.get("strategy", "?") for a in s.get("attempts", [])) or "?"
        detail = str(s.get("detail", ""))[:150]
        print(f"  {s.get('step_num')}. {s.get('action'):<10} {s.get('status'):<9} "
              f"[{strategies}] {detail}")


RUNS = [
    ("P1 docs and wiki, two tabs", [
        {"action": "navigate", "url": "https://docs.python.org/3/",
         "expect": {"url_contains": "docs.python.org", "title_contains": "Python"}},
        {"action": "extract", "name": "py_home"},
        {"action": "assert", "expect": {"text_contains": "documentation"}},
        {"action": "open_tab", "url": "https://en.wikipedia.org/wiki/Web_browser",
         "expect": {"url_contains": "wikipedia.org"}},
        {"action": "extract", "name": "wiki_browser"},
        {"action": "assert", "expect": {"text_contains": "web browser"}},
        {"action": "switch_tab", "tab": {"url_contains": "docs.python.org"},
         "expect": {"url_contains": "docs.python.org"}},
        {"action": "click", "target": 'a[href="tutorial/index.html"]',
         "expect": {"url_contains": "tutorial"}},
        {"action": "assert", "expect": {"text_contains": "Python Tutorial"}},
        {"action": "switch_tab", "tab": {"url_contains": "wikipedia.org"},
         "expect": {"url_contains": "wikipedia.org"}},
        {"action": "close_tab", "tab": {"url_contains": "wikipedia.org"}},
        {"action": "assert", "expect": {"url_contains": "docs.python.org"}},
    ]),
    ("P2 wikipedia search results, follow first result", [
        {"action": "navigate",
         "url": "https://en.wikipedia.org/wiki/Special:Search?search=San%20Francisco&fulltext=1",
         "expect": {"url_contains": "Special:Search"}},
        {"action": "assert", "expect": {"text_contains": "San Francisco"}},
        {"action": "extract", "name": "search_results"},
        {"action": "click", "target": ".mw-search-result-heading a"},
        {"action": "assert", "expect": {"text_contains": "San Francisco"}},
        {"action": "extract", "name": "article"},
    ]),
    ("P3 MDN http overview (client-rendered)", [
        {"action": "navigate", "url": "https://developer.mozilla.org/en-US/docs/Web/HTTP",
         "expect": {"url_contains": "developer.mozilla.org", "title_contains": "HTTP"}},
        {"action": "wait", "target": "h1", "timeout_sec": 10},
        {"action": "extract", "name": "mdn_http"},
        {"action": "assert", "expect": {"text_contains": "Hypertext Transfer Protocol"}},
    ]),
    ("P4 long page scroll", [
        # Wikipedia's canonical title is "History of the internet" (lowercase
        # internet); the capitalized form redirects to it.
        {"action": "navigate", "url": "https://en.wikipedia.org/wiki/History_of_the_Internet",
         "expect": {"url_contains": "History_of_the_internet"}},
        {"action": "extract", "name": "hist_top"},
        {"action": "scroll", "direction": "down", "amount": 4000},
        {"action": "scroll", "direction": "down", "amount": 4000},
        {"action": "scroll", "direction": "down", "to_selector": "#footer"},
        {"action": "assert", "expect": {"url_contains": "History_of_the_internet"}},
    ]),
]

PROBE = [
    {"action": "navigate", "url": "https://www.bbc.com/",
     "expect": {"url_contains": "bbc.com"}},
    {"action": "extract", "name": "probe_home"},
]


async def main():
    from app.services.browser_sandbox import BrowserSandboxService
    from app.services.browser_run import run_browser_steps

    sb = BrowserSandboxService()
    records = {}
    try:
        for label, steps in RUNS:
            try:
                rec = await asyncio.wait_for(
                    run_browser_steps(sb, steps, owner="public-web-suite"), timeout=420)
            except asyncio.TimeoutError:
                rec = {"status": "timeout", "steps": [], "steps_landed": 0,
                       "steps_total": len(steps), "secs": 420}
            records[label] = rec
            show_run(label, rec)
        try:
            probe = await asyncio.wait_for(
                run_browser_steps(sb, PROBE, owner="public-web-probe"), timeout=180)
        except asyncio.TimeoutError:
            probe = {"status": "timeout", "steps": [], "steps_landed": 0,
                     "steps_total": len(PROBE), "secs": 180}
        show_run("PROBE bbc.com (not gated)", probe)
    finally:
        try:
            await sb.stop_browser()
        except Exception:
            pass

    for label, steps in RUNS:
        rec = records[label]
        check(f"{label}: run completed with every step landed",
              rec.get("status") == "completed" and rec.get("steps_landed") == rec.get("steps_total"),
              f"status={rec.get('status')} landed={rec.get('steps_landed')}/{rec.get('steps_total')}")
    p1 = records[RUNS[0][0]]
    check("P1 extract holds the docs home text",
          "Python" in p1.get("extracts", {}).get("py_home", ""), "extract missing or empty")
    check("P1 extract holds the browser article text",
          "browser" in p1.get("extracts", {}).get("wiki_browser", "").lower(), "extract missing or empty")
    check("P1 ended back on one docs tab",
          len(p1.get("final_tabs", [])) == 1 and "docs.python.org" in (p1.get("final_url") or ""),
          f"tabs={p1.get('final_tabs')} final={p1.get('final_url')}")
    p2 = records[RUNS[1][0]]
    check("P2 followed the result into an article page",
          "/wiki/Special:Search" not in (p2.get("final_url") or "") and "/wiki/" in (p2.get("final_url") or ""),
          f"final={p2.get('final_url')}")
    p3 = records[RUNS[2][0]]
    check("P3 extract holds the MDN overview text",
          "Hypertext Transfer Protocol" in p3.get("extracts", {}).get("mdn_http", ""),
          "extract missing or empty")
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
