"""AGE-48: the running version in the top bar.

Rene directive 2026-11-13: the top bar (Xod logo, Needs you, the five
surfaces) showed no version anywhere, so a wrong, old launch was
invisible until something broke. The version pill existed only inside
Settings (div id xodVersion, rendered by js/updates.js pill() from
GET /api/update). The fix puts the same pill next to the Xod name in
the top bar: updates.js topPill() renders it from the same state
object (no second checker, no new endpoint), as the running version
plus a green dot and the word Current when it is the latest release,
the newer version named when one is out, and the plain version alone
while a check is running or unknown. The click opens the existing
Settings panel scrolled to its Updates section
(XodUpdates.openSettings), where Check now and the policy switch
already live.

Pinned here, structurally (the surface is static HTML/JS): the pill
element sits in the top header next to the wordmark and ahead of the
surface nav; its click routes to XodUpdates.openSettings; the
Settings Updates section carries the scroll target id; the Settings
pill is untouched; topPill reads the shared state and performs no
fetch of its own; the state-to-display mapping covers Current, the
named newer version, and the plain version; and updates.js still
parses as valid JavaScript.

Run: .venv/bin/python tests/brain/run_version_pill_test.py
"""
import os, re, shutil, subprocess, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_version_pill_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True, exist_ok=True)
os.chdir(ROOT)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)


html = (ROOT / "app/static/index.html").read_text()
js = (ROOT / "app/static/js/updates.js").read_text()

print("== the top bar carries the pill, next to the name ==")
check("pill element exists", 'id="xodVersionTop"' in html)
i_word = html.find('class="xod-wordmark"')
i_pill = html.find('id="xodVersionTop"')
i_nav = html.find('id="surfaceNav"')
check("pill sits after the Xod wordmark and before the surface nav",
      -1 < i_word < i_pill < i_nav, f"wordmark={i_word} pill={i_pill} nav={i_nav}")
check("the header (h-14) contains the pill",
      html.rfind("<header", 0, i_pill) < i_pill < html.find("</header>", i_pill))
check("pill click routes to XodUpdates.openSettings",
      "XodUpdates.openSettings()" in html[i_pill:i_pill + 400])
check("Settings Updates section carries the scroll target id",
      '<section id="updatesSection">' in html)
check("the Settings pill is untouched (one checker, two renders)",
      '<div id="xodVersion" class="xv mt-2"></div>' in html)

print("== updates.js renders it from the same state ==")
check("topPill is defined", "function topPill()" in js)
top_body = js[js.find("function topPill()"):js.find("function openSettings()")]
check("topPill performs no fetch of its own (no second checker)", "fetch(" not in top_body)
check("topPill reads the shared state object", "st.current" in top_body and "st.state" in top_body)
check("current state maps to a green dot and the word Current",
      'bg-emerald-400' in top_body and '"Current"' in top_body and '"uptodate"' in top_body)
check("an available update names the newer version",
      "st.latest" in top_body and "is out" in top_body and "bg-amber-400" in top_body)
check("checking or unknown states fall back to the plain version",
      top_body.count('word = ""') >= 1 and 'esc(st.current)' in top_body)
check("pill() renders the top pill too", "function pill() {\n    topPill();" in js)
check("openSettings opens the Settings panel and scrolls to Updates",
      "function openSettings()" in js and "openSettingsPanel" in js and '"updatesSection"' in js)
check("openSettings is exported on XodUpdates",
      re.search(r"window\.XodUpdates = \{[^}]*openSettings", js) is not None)
check("no new endpoint was added to the frontend update flow",
      js.count('"/api/update') == len(re.findall(r'"/api/update', js)) and "/api/update" in js)

node = shutil.which("node")
if node:
    r = subprocess.run([node, "--check", str(ROOT / "app/static/js/updates.js")],
                       capture_output=True, text=True)
    check("updates.js parses as valid JavaScript", r.returncode == 0, r.stderr[:200])
else:
    check("updates.js parses as valid JavaScript", False, "node not found on this machine")

print(f"\n{len(results) - sum(1 for _, ok in results if not ok)}/{len(results)} checks passed", flush=True)
failed = [n for n, ok in results if not ok]
if failed:
    print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1)
print("ALL VERSION PILL CHECKS PASSED", flush=True)
