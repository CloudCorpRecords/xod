"""
Phase 2 of the simplicity redesign (collapse the menu):
  - the five surfaces (Ask, Work, Library, Family, Settings) are the
    primary navigation; the 21-item Tools menu is gone
  - every one of the 21 mapped items is reachable in its new home
    (asserted per item against index.html / the surface modules)
  - Settings - Advanced is a drawer, closed by default, holding
    Model routing, Model Lab, Health diagnostics and the Playground
  - Settings - Brain hosts the model picker (its final home)
  - tasks/missions carry the Phase 0-style origin stamp, and scratch
    jobs classify out of the Work feed (display-only, nothing deleted)
  python tests/smoke/run_simplicity_phase2_test.py
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="xod-phase2-"))
os.environ["XOD_DATA_DIR"] = str(TMP)
os.environ.setdefault("XOD_SPEECH", "off")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def main():
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    surfaces = (ROOT / "app" / "static" / "js" / "surfaces.js").read_text(encoding="utf-8")
    family_js = (ROOT / "app" / "static" / "js" / "family.js").read_text(encoding="utf-8")
    activity_js = (ROOT / "app" / "static" / "js" / "activity.js").read_text(encoding="utf-8")
    tasks_js = (ROOT / "app" / "static" / "js" / "tasks.js").read_text(encoding="utf-8")
    missions_js = (ROOT / "app" / "static" / "js" / "missions.js").read_text(encoding="utf-8")
    app_js = (ROOT / "app" / "static" / "js" / "app.js").read_text(encoding="utf-8")

    nav = html.index('id="surfaceNav"')
    lib = html.index('id="libraryPanel"')
    stg = html.index('id="settingsPanel"')
    adv = html.index('id="settingsAdvanced"')
    header_end = html.index("<!-- Workspace Container")

    print("S1 five-surface navigation")
    check("the everything-menu is gone", 'id="toolsMenu"' not in html)
    order = [html.index(f'data-surface="{s}"') for s in ("ask", "work", "library", "family", "settings")]
    check("nav has exactly the five surfaces, in order",
          all(nav < p < header_end for p in order) and order == sorted(order), str(order))
    check("Ask is the active home surface", 'data-surface="ask" onclick="goSurface(\'ask\')" class="surface-nav-btn is-active"' in html)
    check("goSurface routes all five", all(f"'{s}'" in surfaces or f'"{s}"' in surfaces for s in ("ask", "work", "library", "family", "settings"))
          and "goSurface" in surfaces)
    check("status line (face + one sentence) still tops every surface", 'id="xodStatusText">All good.<' in html)
    sidebar_chunk = html[html.index('id="sidebarSessions"'):html.index("<!-- Main Chat Workspace")]
    check("version info moved to Settings (out of the sidebar)",
          html.index('id="xodVersion"') > stg and 'id="xodVersion"' not in sidebar_chunk)

    print("S2 the 21-item mapping, per item")
    in_lib = lambda s: lib < html.index(s) < stg          # noqa: E731
    in_stg = lambda s: stg < html.index(s) < adv          # noqa: E731
    in_adv = lambda s: html.index(s) > adv                # noqa: E731
    check("01 Xod's page -> merged into Work (feed is its home; detail still reachable)",
          'id="tasksPanel"' in html and in_adv("openAgentPanel()"))
    check("02 AI models -> Settings-Brain", in_stg("openModelsPanel()"))
    check("03 Your Xods -> merged into Family", 'data-family-action="xods"' in family_js and "openXodsPanel" in family_js)
    check("04 Health -> cut as a page; detail in Advanced-Diagnostics",
          in_adv("openHealthPanel()") and 'id="healthMenuDot"' in html[adv:])
    check("05 Activity -> Work History tab (with undo)",
          'id="workTabHistory"' in html and 'id="workHistoryBody"' in html and "__xodRenderActivityInto" in activity_js)
    check("06 Xod's look -> Settings-Look", in_stg("openCompanionPicker()"))
    check("07 Model routing -> Settings-Advanced", in_adv("openRoutingPanel()"))
    check("08 Media Studio -> Library-Media", in_lib("openMediaStudioModal()"))
    check("09 Memory -> Library-Memory", in_lib("openMemoryVaultModal()"))
    check("10 Your phone -> Family device card/panel", 'data-family-action="phone"' in family_js and "openPhonePanel" in family_js)
    check("11 Family -> Family surface", 'data-surface="family"' in html and "openFamilyPanel()" in html)
    check("12 Routines & goals -> Work section", 'id="workTabRoutines"' in html and "openRoutinesPanel()" in html)
    check("13 Connected apps -> Settings-Connections", in_stg("openMcpPanel()"))
    check("14 Xod's heart -> cut as a destination; journal filed in Library-Memory",
          "Xod's heart" not in html and "journal lives here too" in html[lib:stg])
    check("15 Model Lab -> Settings-Advanced", in_adv("openModelLabModal()"))
    check("16 Businesses for sale -> Library-Collections", in_lib("openDealsModal()"))
    check("17 People & emails -> Library-Collections", in_lib("openHunterSuiteModal()"))
    check("18 Project map -> a view inside Library-Projects", in_lib("openObservabilityModal()"))
    check("19 Site tech lookup -> cut from the UI, composer capability only",
          "openTechAuditModal" not in html and "function openTechAuditModal" in app_js)
    check("20 Startup library -> Library-Collections", in_lib("openKnowledgeGraphModal()"))
    check("21 Computer files -> Library-Files", in_lib("openComputerFileManagerModal()"))
    check("Agent Mail -> Family-Messages (relocation only)",
          'data-family-action="messages"' in family_js and "openMailPanel" in family_js and 'id="mailBadge"' in html)
    check("device cards: friendly name + one status line",
          "last seen" in family_js and "device card" in family_js)
    all_js = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "app" / "static" / "js").glob("*.js"))
    check("old openers all still exist (deep links resolve)",
          all(s in all_js for s in
              ("openHealthPanel", "openRoutingPanel", "openModelsPanel", "openMcpPanel",
               "openXodsPanel", "openPhonePanel", "openMailPanel", "openCompanionPicker")))

    print("S3 Settings structure")
    check("Advanced drawer is closed by default", '<details id="settingsAdvanced">' in html)
    adv_html = html[adv:]
    check("Advanced holds routing, lab, diagnostics, playground",
          all(s in adv_html for s in ("openRoutingPanel()", "openModelLabModal()", "openHealthPanel()", 'id="settingsPlayground"')))
    check("Settings-Brain hosts the model picker (final home)",
          stg < html.index('id="modelSwitcherBtn"') < html.index('id="modelSwitcherPanel"') < adv)
    check("the picker left the status detail; a summary mirror stays",
          html.index('id="modelSwitcherBtn"') > html.index('id="localServersBtn"') and 'id="statusBrainMirror"' in html)
    check("connection status rows preserved in Settings-Connections",
          all(f'id="{i}"' in html[stg:adv] for i in ("llmStatusText", "trustmrrStatusText", "youStatusText", "tavilyStatusText", "youtubeStatusText")))

    print("S4 origin stamp: scratch tasks/missions classify out of the Work feed")
    from app.services import task_queue as tq
    from app.services import missions as ms

    real = tq.task_queue.create("Sort the Downloads folder", title="Sort the Downloads folder")
    check("a normal task is real", real["origin"] == "user" and real["scratch"] is False, str(real.get("origin")))
    proving = tq.task_queue.create("Create the proof directory", title="Create proof directory", origin="proving")
    check("a proving-stamped task is scratch", proving["origin"] == "proving" and proving["scratch"] is True)
    legacy = tq.task_queue.create("anything", title="Proof file")
    check("legacy debris (no stamp) is caught by the Phase 0 name check", legacy["scratch"] is True)
    listed = {t["id"]: t for t in tq.task_queue.list()}
    check("list() carries origin + scratch for the feed filter",
          listed[proving["id"]]["scratch"] is True and listed[real["id"]]["scratch"] is False)
    counts = tq.task_queue.counts()
    check("counts() excludes stamped scratch jobs", counts.get("queued", 0) == 2, str(counts))
    check("nothing was deleted: the scratch task is still stored", tq.task_queue.get(proving["id"]) is not None)

    m_real = ms.create_mission("Tidy the photo library", origin="user")
    check("a normal mission is real", m_real["origin"] == "user" and m_real["scratch"] is False, str(m_real.get("origin")))
    m_scratch = ms.create_mission("Build the word frequency tool", origin="proving")
    check("a proving-stamped mission is scratch", m_scratch["origin"] == "proving" and m_scratch["scratch"] is True)
    by_id = {m["id"]: m for m in ms.list_missions()}
    check("list_missions() carries the scratch flag", by_id[m_scratch["id"]]["scratch"] is True and by_id[m_real["id"]]["scratch"] is False)
    check("feed filters are wired in the UI", "!t.scratch" in tasks_js and "m.scratch" in missions_js)
    missions_py = (ROOT / "app" / "services" / "missions.py").read_text(encoding="utf-8")
    check("mission steps inherit the mission's origin stamp",
          'origin=(get_mission(mission_id) or {}).get("origin") or "user"' in missions_py)

    failed = [n for n, ok in results if not ok]
    print(f"SUMMARY {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)} checks passed"
          + (f" — failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
