"""
Computer Health (spec: XOD-COMPUTER-HEALTH-SPEC-2026-11-06; directive 2026-11-08).

Spawns the 2026-11-04 leak shape in miniature and proves Xod now catches and cleans it:

  A  three headless Chromes on temp profiles, orphaned (their launcher exits, exactly
     like the test runs that leaked them), are flagged leaked_test_browser with evidence
  B  a duplicate singleton helper is flagged duplicate_singleton
  C  Tier 1 cleanup TERMs the three browsers and the duplicate, and nothing else:
     a co-launched VISIBLE Chrome and an owned headless browser on a normal profile
     are untouched, and the oldest helper survives
  D  the age floor is real: with the production 2-hour floor, the same young fakes
     produce no leaked finding
  E  a LIGHT census (the under-pressure adaptive mode) sees the same shape but only
     at Tier 2, and Tier 1 acts on none of it: weaker evidence never earns an action
  F  detection is evidence-based for the other rules too (synthetic censuses): an
     orphaned hog needs a sustained-CPU second sample, memory pressure fires on free %
     and on swap growth, and local models over the 40%-of-RAM budget are flagged
  G  the readout is rendered from the census (its free-% number matches), the health
     file is written, and the service's own cost is measured against its budget

The fakes are copies of the Python interpreter renamed to look like the real processes
on a command line ("Google Chrome", "xod_partner_test"), each running a sleeper. The
test config scopes the leaked rule to this test's own profile marker and singleton
marker, so a run on a shared machine can only ever touch its own fakes.

Run: .venv/bin/python tests/features/run_computer_health_test.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ["XOD_DATA_DIR"] = os.environ.get("XOD_TEST_DATA_DIR") or tempfile.mkdtemp(prefix="xod-health-")
sys.path.insert(0, str(ROOT))

from app.paths import DATA_DIR  # noqa: E402
from app.services import computer_health as CH  # noqa: E402

RUN = DATA_DIR / "test_runs" / "computer_health"
RUN.mkdir(parents=True, exist_ok=True)
SLEEPER = "import time; time.sleep(3600)"
results = []


def report(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def spawn_detached(exe: Path, args, tag: str) -> int:
    """Launch through a middleman that exits at once, so the child is orphaned to
    init/launchd - the exact shape of the Nov 4 leaks. Returns the child's pid."""
    pidfile = RUN / f"pid-{tag}.txt"
    code = (
        "import subprocess, sys, pathlib;"
        f"p = subprocess.Popen([{str(exe)!r}, '-c', {SLEEPER!r}] + {list(args)!r},"
        " start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL);"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(p.pid))"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)
    for _ in range(50):
        if pidfile.exists():
            return int(pidfile.read_text().strip())
        time.sleep(0.1)
    raise RuntimeError(f"spawner for {tag} never wrote a pid")


def main():
    bindir = RUN / "bin"
    bindir.mkdir(exist_ok=True)
    chrome_exe = bindir / "Google Chrome"
    partner_exe = bindir / "xod_partner_test"
    for dst in (chrome_exe, partner_exe):
        if not dst.exists():
            shutil.copy(sys.executable, dst)
            dst.chmod(0o755)

    spawned = []
    try:
        # --- the Nov 4 shape, in miniature -------------------------------------
        leaked = []
        for i in range(3):
            profile = RUN / "profiles" / f"xod-studio-test-{i}"
            profile.mkdir(parents=True, exist_ok=True)
            pid = spawn_detached(chrome_exe, ["--headless=new", f"--user-data-dir={profile}",
                                              f"--remote-debugging-port={19311 + i}"], f"leak{i}")
            leaked.append(pid)
            spawned.append(pid)
        older = spawn_detached(partner_exe, ["worker"], "partner-old")
        spawned.append(older)
        time.sleep(1.2)                       # make the duplicate measurably younger
        younger = spawn_detached(partner_exe, ["worker"], "partner-young")
        spawned.append(younger)
        home_profile = RUN / "fakehome" / "User Data"
        home_profile.mkdir(parents=True, exist_ok=True)
        visible = subprocess.Popen([str(chrome_exe), "-c", SLEEPER, f"--user-data-dir={home_profile}"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid
        owned = subprocess.Popen([str(chrome_exe), "-c", SLEEPER, "--headless=new",
                                  f"--user-data-dir={home_profile}"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid
        spawned += [visible, owned]
        time.sleep(1.2)                       # let ages register

        test_cfg = CH.default_config()
        test_cfg.update({
            "leaked_browser_age_sec": 0,
            "leak_profile_marker": "xod-studio-test-",
            "singletons": {"partner": {"markers": ("xod_partner_test",), "tier1_dedupe": True}},
        })

        # --- E: light census downgrades, never acts -----------------------------
        light_census = CH.collect_census(test_cfg, None, light=True)
        light_findings = CH.detect(light_census, test_cfg)
        light_leaks = [f for f in light_findings if f["rule"] == "leaked_test_browser"]
        report("E light census flags the leaks at Tier 2 only",
               len(light_leaks) == 3 and all(f["tier"] == 2 for f in light_leaks),
               f"{len(light_leaks)} leaks")
        leak_only = [f for f in light_findings if f["rule"] == "leaked_test_browser"]
        acts = CH.apply_tier1(light_census, leak_only)
        report("E Tier 1 acts on no browser from a light census",
               acts == [] and all(alive(p) for p in leaked), f"actions={acts}")

        # --- D: the production age floor ---------------------------------------
        default_findings = CH.detect(CH.collect_census(CH.default_config(), None), test_cfg | {
            "leaked_browser_age_sec": CH.default_config()["leaked_browser_age_sec"]})
        report("D production age floor flags none of the young fakes",
               not [f for f in default_findings if f["rule"] == "leaked_test_browser"
                    and f["pid"] in leaked])

        # --- A + B: full census, right rules, right pids ------------------------
        census = CH.collect_census(test_cfg, None)
        findings = CH.detect(census, test_cfg)
        leaks = [f for f in findings if f["rule"] == "leaked_test_browser"]
        report("A three leaked browsers flagged with evidence",
               sorted(f["pid"] for f in leaks) == sorted(leaked)
               and all(f["tier"] == 1 and "xod-studio-test-" in f["evidence"]["profile"]
                       for f in leaks),
               f"flagged={sorted(f['pid'] for f in leaks)} spawned={sorted(leaked)}")
        fams = {r["pid"]: r["family"] for r in census["processes"]}
        report("A visible Chrome is classified visible, never headless",
               fams.get(visible) == "chrome_visible" and fams.get(owned) == "chrome_headless")
        dup = [f for f in findings if f["rule"] == "duplicate_singleton"]
        report("B duplicate singleton flagged, oldest is the keeper",
               len(dup) == 1 and dup[0]["keep_pid"] == older and dup[0]["extra_pids"] == [younger],
               f"{dup}")

        # --- F: the other rules, on synthetic censuses ---------------------------
        base_mem = {"total_gb": 16.0, "free_pct": 60.0, "swap_used_gb": 1.0, "swap_total_gb": 12.0}
        hog_proc = {"pid": 4242, "ppid": 1, "name": "stray", "cmdline": "/usr/bin/stray --run",
                    "family": "other", "age_sec": 7200, "rss_mb": 200.0,
                    "cpu_pct_sustained": None, "never_touch": False}
        c1 = {"at": time.time(), "memory": dict(base_mem), "processes": [hog_proc],
              "model_engines": {"rss_mb": 0, "processes": []}}
        report("F no hog call without a second CPU sample",
               not [f for f in CH.detect(c1) if f["rule"] == "orphaned_hog"])
        c2 = json.loads(json.dumps(c1))
        c2["processes"][0]["cpu_pct_sustained"] = 95.0
        report("F sustained CPU across two samples flags the hog (Tier 2)",
               any(f["rule"] == "orphaned_hog" and f["tier"] == 2 for f in CH.detect(c2)))
        c3 = json.loads(json.dumps(c1))
        c3["memory"]["free_pct"] = 9.0
        c3["memory"]["swap_growth_gb_per_hour"] = 2.5
        report("F memory pressure fires on free % and swap growth",
               any(f["rule"] == "memory_pressure" for f in CH.detect(c3)))
        c4 = json.loads(json.dumps(c1))
        c4["model_engines"] = {"rss_mb": 8.0 * 1024, "processes": []}
        report("F local models over the 40% budget are flagged",
               any(f["rule"] == "model_over_budget" for f in CH.detect(c4)))

        # --- C + G: a full check cleans Tier 1, spares the rest, writes the file --
        latest = CH.run_check(test_cfg)
        term = [a for a in latest["actions"] if a["action"] == "term_leaked_browser" and a["ok"]]
        dedupe = [a for a in latest["actions"] if a["action"] == "dedupe_singleton" and a["ok"]]
        time.sleep(0.6)
        report("C Tier 1 freed the three leaked browsers and the duplicate",
               len(term) == 3 and len(dedupe) == 1
               and not any(alive(p) for p in leaked) and not alive(younger),
               f"actions={latest['actions']}")
        report("C visible Chrome, owned headless, and the oldest helper are untouched",
               alive(visible) and alive(owned) and alive(older))
        free_txt = f"({latest['census']['memory']['free_pct']:.0f}% free)"
        readout = "\n".join(latest["readout"])
        report("G readout matches the census and names the cleanup",
               free_txt in readout and "3 leftover test browsers" in readout
               and "Cleaned up a leftover test browser." in readout,
               f"readout={latest['readout']}")
        saved = json.loads((DATA_DIR / "computer-health.json").read_text())
        report("G health file written with census, findings, actions",
               saved["latest"]["census"]["xod_footprint"] is not None
               and len(saved["latest"]["findings"]) >= 4 and len(saved["latest"]["actions"]) >= 4)
        cost = CH.measure_own_cost()
        report("G own cost measured against the weak-hardware budget",
               cost["check_seconds"] <= 5.0,
               f"check={cost['check_seconds']}s rss={cost['process_rss_mb']}MB budget={cost['budget']}")
    finally:
        for pid in spawned:
            try:
                os.kill(pid, 9)
            except OSError:
                pass

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        sys.exit(1)
    print("ALL COMPUTER HEALTH TESTS PASSED", flush=True)


if __name__ == "__main__":
    main()
