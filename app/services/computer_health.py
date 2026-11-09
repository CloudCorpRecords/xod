"""
Computer Health: Xod watches the computer it lives on, so the machine does not fill up
with the agent layer's own leftovers until something visible breaks.

Why this exists (all from the record, spec: XOD-COMPUTER-HEALTH-SPEC-2026-11-06):
  2026-11-04  test runs left 12 headless Chrome mains and 131 Chrome processes on the Mac,
              some 1-2 days old; "Chrome won't launch" was how the human found out.
  2026-11-02  two ~6.5 GB local models loaded by two engines at once collapsed a 16 GB
              machine (wired 12.16 GB, swap nearly full). The model budget gate is one of
              this service's detectors (see detect(): model_over_budget).
  2026-11-05  Xod Partner ran as TWO instances for days; five leftover dev backends and
              two orphaned Xod.app backends were found the same morning.

What it does, every check (default 5 minutes, read-only census first):
  - Memory: free %, wired, compressed (macOS vm_stat), swap used and swap growth rate.
  - Process census by family: Chrome visible vs headless (each headless main's profile
    dir and age), the Xod family (this backend, the desktop app, Partner, helpers, and
    Xod's TOTAL footprint as a first-class number), local model engines and their
    resident footprints, dev servers with ages.
  - Singleton audit: Partner and the Xod backend must be exactly one instance each.
  - Detection rules with evidence attached (detect()): leaked test browser, duplicate
    singleton, orphaned hog (two samples before anything is called a hog), stale dev
    server, memory pressure, local models over the memory budget.
  - Laddered actions (apply_tier1()): Tier 0 report only; Tier 1 auto only for the
    proven-safe classes (TERM a leaked test browser, dedupe a singleton helper keeping
    the oldest, stop Xod's own built-in engine when memory is pressured and it is idle);
    everything else is a Tier 2 finding for an approval card and is NEVER acted on here.
  - NEVER touched at any tier: the user's visible Chrome and its helpers, call/meeting apps,
    the frontmost app, the Muse app, System Settings, anything younger than its rule's
    age floor, and anything whose identity cannot be established from its command line.

Where it lives: the lightweight backend layer (this service), not the Electron app, so
it can run permanently in the background. It depends only on psutil, the stdlib, the
data-dir path, and (lazily, only when a pressure action fires) local_servers.stop() for
Xod's own built-in engine. Its own cost is budgeted and measured: see OWN_BUDGET and
the "self" block every census carries.

One health file per machine in the Xod data dir (computer-health.json) holds the latest
census, findings, actions, the plain-language readout, and recent history; every check,
finding, and action is also appended to computer-health-log.jsonl with before/after
census numbers.
"""
import asyncio
import json
import logging
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from app.paths import DATA_DIR

logger = logging.getLogger("xod.computer_health")

HEALTH_PATH = DATA_DIR / "computer-health.json"
LOG_PATH = DATA_DIR / "computer-health-log.jsonl"
HISTORY_KEEP = 288                      # one day of 5-minute checks
GB = 1024 ** 3

# This service's own footprint budget, set by WEAK hardware (a 4-8 GB old laptop),
# not by what a 16 GB Mac tolerates. Measured by run_computer_health_test.py, by the
# standalone self-measurement in measure_own_cost(), and by the census "self" block.
#   rss_mb_ceiling:        the whole health service process, interpreter included.
#   rss_incremental_mb:    what the module itself adds over a bare interpreter.
#   census_seconds:        wall time of one full census on a slow machine.
#   idle_cpu_pct_ceiling:  averaged over the 5-minute cadence (1s of census per 300s
#                          is 0.33%, so the ceiling leaves a little headroom).
OWN_BUDGET = {"rss_mb_ceiling": 64.0, "rss_incremental_mb": 25.0,
              "census_seconds": 1.0, "idle_cpu_pct_ceiling": 0.5}

# Process families that must never be acted on, matched on the command line / exe name.
NEVER_TOUCH_MARKERS = (
    "zoom.us", "zoom ", "zoom.app", "facetime", "microsoft teams", "webex",
    "claude.app", "/claude ", "system settings", "systemsettings", "system preferences",
)
# Singleton classes: key -> (cmdline markers, tier-1 dedupe allowed).
# Partner dedupe is Tier 1 (the 2026-11-06 restart converged exactly this way: keep one
# launcher + one worker). A duplicate BACKEND is reported Tier 2 only: two backends can
# be a deliberate scratch instance next to production, so a human decides.
SINGLETONS = {
    "partner": {"markers": ("xod_partner",), "tier1_dedupe": True},
    "xod_backend": {"markers": ("app.main", "xod_node", "services.backend"), "tier1_dedupe": False},
}
CHROME_EXE_MARKERS = ("google chrome", "chromium", "chrome.exe", "chrome for testing", "chrome-headless-shell")
MODEL_ENGINE_MARKERS = ("ollama", "lm studio", "lmstudio", "llama-server", "llama_server", "mlx_lm", "local_engine")
DEV_SERVER_MARKERS = ("uvicorn", "gunicorn", "flask run", "django", "node ", "vite", "next-server", "webpack")


def default_config() -> Dict[str, Any]:
    """Rule thresholds. Tests override the age floors; production uses the spec's."""
    raw_interval = os.environ.get("XOD_HEALTH_INTERVAL_SEC", "")
    try:
        interval = int(raw_interval) if raw_interval else 300
    except ValueError:
        interval = 300
    return {
        "interval_sec": interval,
        "leaked_browser_age_sec": 2 * 3600,
        "hog_age_sec": 3600,
        "hog_cpu_pct": 80.0,
        "hog_rss_gb": 1.5,
        "stale_dev_age_sec": 12 * 3600,
        "free_pct_floor": 15.0,
        "swap_growth_gb_per_hour": 1.0,
        "model_budget_frac": 0.40,          # FIX C: total local models <= 40% of RAM
        "history_keep": HISTORY_KEEP,
        # Scoping hooks (tests use them so a test run can only ever touch its own
        # fakes; production leaves both unset and gets the full rules).
        "leak_profile_marker": None,        # if set, leaked rule only matches this marker
        "singletons": None,                 # if set, replaces SINGLETONS
    }


# ---------------------------------------------------------------- census helpers

def _cmdline(pinfo: Dict[str, Any]) -> str:
    return " ".join(pinfo.get("cmdline") or [])


def _is_chrome_main(cmd: str, low: str) -> bool:
    """A Chrome main process: chrome exe, a user-data-dir, and no --type= (helpers have one)."""
    if "--type=" in cmd or "--user-data-dir=" not in cmd:
        return False
    exe = low.split("--", 1)[0]
    return any(m in exe for m in CHROME_EXE_MARKERS) or "chrome" in exe


def _profile_dir(cmd: str) -> str:
    for part in cmd.split(" "):
        if part.startswith("--user-data-dir="):
            return part.split("=", 1)[1].strip('"')
    return ""


def _debug_port(cmd: str) -> Optional[int]:
    for part in cmd.split(" "):
        if part.startswith("--remote-debugging-port="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                return None
    return None


def is_temp_profile(profile: str) -> bool:
    """A throwaway browser profile: Xod test profiles or anything under a temp root."""
    if not profile:
        return False
    base = os.path.basename(profile.rstrip("/"))
    if base.startswith("xod-studio-") or base == "xod_ai_browser_sandbox" or base.startswith("xod-heal-"):
        return True
    if base in ("User Data", "Default"):
        return False                    # a normal profile shape, wherever it sits
    temp_roots = {tempfile.gettempdir(), "/tmp", "/var/folders", os.environ.get("TMPDIR") or ""}
    p = os.path.abspath(profile)
    return any(root and (p == root or p.startswith(root.rstrip("/") + "/")) for root in temp_roots)


def _never_touch(cmd_low: str, name_low: str) -> bool:
    hay = cmd_low + " " + name_low
    return any(m in hay for m in NEVER_TOUCH_MARKERS)


def _family_of(cmd: str, low: str, name_low: str) -> str:
    if _is_chrome_main(cmd, low):
        return "chrome_headless" if "--headless" in low else "chrome_visible"
    if "chrome" in low or "chromium" in low:
        return "chrome_helper"
    if any(m in low for m in MODEL_ENGINE_MARKERS):
        return "model_engine"
    if "xod_partner" in low:
        return "xod"
    if "xod.app" in low or "/xod/" in low or "app.main" in low or "xod_node" in low:
        return "xod"
    if any(m in low for m in DEV_SERVER_MARKERS):
        return "dev_server"
    return "other"


def _darwin_memory_extra() -> Dict[str, Any]:
    """Wired and compressor numbers, which psutil does not expose on macOS."""
    if sys.platform != "darwin":
        return {}
    try:
        import subprocess
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        page = 16384
        vals = {}
        for line in out.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            try:
                vals[k.strip()] = int(v.strip().rstrip("."))
            except ValueError:
                continue
        wired = vals.get("Pages wired down", 0) * page
        compressed = vals.get("Pages occupied by compressor", 0) * page
        return {"wired_gb": round(wired / GB, 2), "compressed_gb": round(compressed / GB, 2)}
    except Exception:
        return {}


def collect_census(config: Optional[Dict[str, Any]] = None, prev: Optional[Dict[str, Any]] = None,
                   light: bool = False) -> Dict[str, Any]:
    """One read-only pass over the machine. Cheap: a single process_iter sweep plus one
    net_connections call. CPU-per-process is computed against the PREVIOUS census
    (cpu_seconds deltas), which is what makes the hog rule a two-sample rule.

    light=True is the adaptive mode for a machine already under pressure: it skips the
    net_connections sweep (the most expensive call on a loaded box), so CDP evidence
    comes back "not checked" and detect() downgrades anything that would have needed
    it to Tier 2. A health monitor must never add load during a meltdown, and must
    never act on weaker evidence either."""
    started = time.time()
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    census: Dict[str, Any] = {
        "at": started,
        "memory": {
            "total_gb": round(vm.total / GB, 2),
            "free_pct": round(vm.available / vm.total * 100, 1) if vm.total else 0.0,
            "used_pct": round(vm.percent, 1),
            "swap_used_gb": round(swap.used / GB, 2),
            "swap_total_gb": round(swap.total / GB, 2),
            **_darwin_memory_extra(),
        },
        "load": list(os.getloadavg()) if hasattr(os, "getloadavg") else [],
        "processes": [],
        "families": {},
        "xod_footprint": {"rss_mb": 0.0, "count": 0, "lines": []},
        "self": {},
    }
    if prev and prev.get("memory"):
        dt_h = max((started - prev["at"]) / 3600.0, 1e-6)
        growth = (census["memory"]["swap_used_gb"] - prev["memory"]["swap_used_gb"]) / dt_h
        census["memory"]["swap_growth_gb_per_hour"] = round(growth, 3)

    busy_debug_ports = set()
    if not light:
        try:
            conns = psutil.net_connections(kind="tcp")
        except Exception:
            conns = []
        busy_debug_ports = {c.laddr.port for c in conns
                            if c.status == psutil.CONN_ESTABLISHED and c.laddr and c.raddr}
    census["light"] = light

    prev_cpu = (prev or {}).get("_cpu_seconds") or {}
    cpu_seconds: Dict[int, float] = {}
    prev_at = (prev or {}).get("at") or started
    wall = max(started - prev_at, 0.001)

    procs: List[Dict[str, Any]] = []
    for p in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time", "memory_info", "cpu_times"]):
        try:
            info = p.info
            cmd = _cmdline(info)
            low = cmd.lower()
            name_low = (info.get("name") or "").lower()
            rss = (info.get("memory_info").rss if info.get("memory_info") else 0) or 0
            ct = info.get("cpu_times")
            cpu_total = (ct.user + ct.system) if ct else 0.0
            cpu_seconds[info["pid"]] = cpu_total
            cpu_pct = None
            if info["pid"] in prev_cpu:
                cpu_pct = round(max(cpu_total - prev_cpu[info["pid"]], 0.0) / wall * 100, 1)
            fam = _family_of(cmd, low, name_low)
            rec = {
                "pid": info["pid"], "ppid": info.get("ppid"), "name": info.get("name") or "",
                "cmdline": cmd[:400], "family": fam,
                "age_sec": round(started - (info.get("create_time") or started)),
                "rss_mb": round(rss / 1024 ** 2, 1), "cpu_pct_sustained": cpu_pct,
                "never_touch": _never_touch(low, name_low),
            }
            if fam in ("chrome_headless", "chrome_visible"):
                rec["profile"] = _profile_dir(cmd)
                rec["debug_port"] = _debug_port(cmd)
                # None = not checked (light census); detect() will not Tier-1 on None.
                rec["cdp_client_connected"] = None if light else bool(
                    rec["debug_port"] and rec["debug_port"] in busy_debug_ports)
            procs.append(rec)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    census["processes"] = procs
    census["_cpu_seconds"] = cpu_seconds

    fams: Dict[str, Dict[str, Any]] = {}
    for rec in procs:
        f = fams.setdefault(rec["family"], {"count": 0, "rss_mb": 0.0})
        f["count"] += 1
        f["rss_mb"] = round(f["rss_mb"] + rec["rss_mb"], 1)
    census["families"] = fams

    xod = [r for r in procs if r["family"] == "xod"]
    lines: Dict[str, float] = {}
    for r in xod:
        key = "partner" if "xod_partner" in r["cmdline"].lower() else \
              "desktop_app" if "xod.app" in r["cmdline"].lower() else "backend"
        lines[key] = round(lines.get(key, 0.0) + r["rss_mb"], 1)
    census["xod_footprint"] = {
        "rss_mb": round(sum(r["rss_mb"] for r in xod), 1), "count": len(xod),
        "lines": [{"part": k, "rss_mb": v} for k, v in sorted(lines.items())],
    }
    engines = [r for r in procs if r["family"] == "model_engine"]
    census["model_engines"] = {
        "rss_mb": round(sum(r["rss_mb"] for r in engines), 1), "count": len(engines),
        "processes": [{"pid": r["pid"], "name": r["name"], "rss_mb": r["rss_mb"],
                       "age_sec": r["age_sec"], "cmdline": r["cmdline"][:160]} for r in engines],
    }
    try:
        me = psutil.Process()
        census["self"] = {"pid": me.pid, "backend_rss_mb": round(me.memory_info().rss / 1024 ** 2, 1)}
    except Exception:
        census["self"] = {}
    census["census_seconds"] = round(time.time() - started, 3)
    census["self"]["census_seconds"] = census["census_seconds"]
    return census


# ---------------------------------------------------------------- detection

def detect(census: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Pure: census in, findings out. Every finding carries its evidence and the tier at
    which it may be acted on. tier 1 = apply_tier1 may act; tier 2 = approval card only;
    tier 0 = report only."""
    cfg = config or default_config()
    findings: List[Dict[str, Any]] = []
    procs = census["processes"]
    now = census["at"]

    for r in procs:
        if r["family"] != "chrome_headless" or r["never_touch"]:
            continue
        profile = r.get("profile") or ""
        marker = cfg.get("leak_profile_marker")
        if marker and marker not in profile:
            continue
        if is_temp_profile(profile) and r["age_sec"] > cfg["leaked_browser_age_sec"]:
            cdp = r.get("cdp_client_connected")
            if cdp:
                continue                    # a client is using it: not a leak
            # Full evidence (CDP checked, nobody connected) earns Tier 1. A light
            # census never checked, so the same shape is Tier 2: ask, don't act.
            findings.append({
                "rule": "leaked_test_browser", "tier": 1 if cdp is False else 2, "pid": r["pid"],
                "summary": f"Leftover test browser (pid {r['pid']}) is holding {r['rss_mb']:.0f} MB.",
                "evidence": {"profile": profile, "age_hours": round(r["age_sec"] / 3600, 1),
                             "rss_mb": r["rss_mb"], "ppid": r["ppid"],
                             "cdp_client_connected": cdp},
            })

    for key, spec in (cfg.get("singletons") or SINGLETONS).items():
        members = [r for r in procs if any(m in r["cmdline"].lower() for m in spec["markers"])]
        if len(members) > 1:
            members.sort(key=lambda r: (now - r["age_sec"]))
            findings.append({
                "rule": "duplicate_singleton", "tier": 1 if spec["tier1_dedupe"] else 2,
                "singleton": key, "keep_pid": members[0]["pid"],
                "extra_pids": [r["pid"] for r in members[1:]],
                "summary": f"{len(members)} copies of the {key} helper are running; only one should be.",
                "evidence": {"pids": [r["pid"] for r in members],
                             "ages_sec": [r["age_sec"] for r in members]},
            })

    for r in procs:
        if r["ppid"] != 1 or r["never_touch"] or r["family"] in ("chrome_headless",):
            continue
        cpu = r.get("cpu_pct_sustained")
        hog = (cpu is not None and cpu > cfg["hog_cpu_pct"]) or r["rss_mb"] > cfg["hog_rss_gb"] * 1024
        if hog and r["age_sec"] > cfg["hog_age_sec"] and r["cmdline"]:
            findings.append({
                "rule": "orphaned_hog", "tier": 2, "pid": r["pid"],
                "summary": f"A background process (pid {r['pid']}) has been running hot for "
                           f"{round(r['age_sec'] / 3600, 1)} hours.",
                "evidence": {"rss_mb": r["rss_mb"], "cpu_pct_sustained": cpu,
                             "age_hours": round(r["age_sec"] / 3600, 1), "cmdline": r["cmdline"][:200]},
            })
        if r["family"] == "dev_server" and r["age_sec"] > cfg["stale_dev_age_sec"]:
            findings.append({
                "rule": "stale_dev_server", "tier": 2, "pid": r["pid"],
                "summary": f"A dev server (pid {r['pid']}) has been up for {round(r['age_sec'] / 3600, 1)} hours.",
                "evidence": {"age_hours": round(r["age_sec"] / 3600, 1), "cmdline": r["cmdline"][:200]},
            })

    mem = census["memory"]
    reasons = []
    if mem["free_pct"] < cfg["free_pct_floor"]:
        reasons.append(f"free memory is {mem['free_pct']}% (floor {cfg['free_pct_floor']:.0f}%)")
    growth = mem.get("swap_growth_gb_per_hour")
    if growth is not None and growth > cfg["swap_growth_gb_per_hour"]:
        reasons.append(f"swap is growing {growth} GB per hour")
    if reasons:
        findings.append({
            "rule": "memory_pressure", "tier": 0,
            "summary": "Memory pressure: " + "; ".join(reasons) + ".",
            "evidence": {"free_pct": mem["free_pct"], "swap_used_gb": mem["swap_used_gb"],
                         "swap_growth_gb_per_hour": growth, "wired_gb": mem.get("wired_gb")},
        })

    engines_gb = census.get("model_engines", {}).get("rss_mb", 0) / 1024
    budget_gb = mem["total_gb"] * cfg["model_budget_frac"]
    if engines_gb > budget_gb:
        findings.append({
            "rule": "model_over_budget", "tier": 0,
            "summary": f"Local model engines are holding {engines_gb:.1f} GB, over the "
                       f"{budget_gb:.1f} GB budget for this {mem['total_gb']:.0f} GB machine.",
            "evidence": {"engines_gb": round(engines_gb, 2), "budget_gb": round(budget_gb, 2),
                         "engines": census.get("model_engines", {}).get("processes", [])},
        })
    return findings


# ---------------------------------------------------------------- actions

def _term(pid: int) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def apply_tier1(census: Dict[str, Any], findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Act only on Tier 1 findings, only on the proven-safe classes. Returns the action
    log; every entry is also written to the health log by run_check()."""
    by_pid = {r["pid"]: r for r in census["processes"]}
    actions: List[Dict[str, Any]] = []
    for f in findings:
        if f.get("tier") != 1:
            continue
        if f["rule"] == "leaked_test_browser":
            rec = by_pid.get(f["pid"])
            if rec is None or rec["never_touch"] or rec["family"] != "chrome_headless":
                continue
            ok = _term(f["pid"])
            actions.append({"action": "term_leaked_browser", "pid": f["pid"], "ok": ok,
                             "rss_mb_freed": rec["rss_mb"] if ok else 0,
                             "profile": rec.get("profile") or ""})
        elif f["rule"] == "duplicate_singleton":
            for pid in f.get("extra_pids", []):
                rec = by_pid.get(pid)
                if rec is None or rec["never_touch"]:
                    continue
                ok = _term(pid)
                actions.append({"action": "dedupe_singleton", "singleton": f["singleton"],
                                "pid": pid, "kept_pid": f["keep_pid"], "ok": ok})
    return actions


async def pressure_relief(census: Dict[str, Any], findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """On memory pressure, stop Xod's OWN built-in engine if it is running and idle.
    That engine is Xod-managed end to end, so this is the memory-cap lifecycle's Tier 1.
    Models inside Ollama / LM Studio are never unloaded from here: those engines may be
    the user's own (local_servers doctrine), so they surface as Tier 2 findings instead."""
    if not any(f["rule"] == "memory_pressure" for f in findings):
        return []
    try:
        from app.services import local_servers
        rep = await local_servers.report()
        actions = []
        for s in rep.get("servers", []):
            if s["id"] == "builtin" and s.get("running") and not s.get("in_use"):
                idle = s.get("idle_seconds") or 0
                if idle >= 60:
                    r = await local_servers.stop("builtin", reason="memory_pressure")
                    actions.append({"action": "stop_idle_builtin_engine", "ok": bool(r.get("success")),
                                    "memory_gb": s.get("memory_gb"), "idle_seconds": idle})
        return actions
    except Exception as e:
        logger.warning(f"pressure relief failed: {e}")
        return []


# ---------------------------------------------------------------- readout + files

def render_readout(census: Dict[str, Any], findings: List[Dict[str, Any]],
                   actions: List[Dict[str, Any]]) -> List[str]:
    """Plain language, rendered from the census so the words always match the numbers."""
    mem = census["memory"]
    lines = []
    if mem["free_pct"] >= 25:
        lines.append(f"Memory is fine ({mem['free_pct']:.0f}% free).")
    elif mem["free_pct"] >= 15:
        lines.append(f"Memory is getting tight ({mem['free_pct']:.0f}% free).")
    else:
        lines.append(f"Memory is under pressure ({mem['free_pct']:.0f}% free).")
    lines.append(f"Xod itself is using {census['xod_footprint']['rss_mb']:.0f} MB "
                 f"across {census['xod_footprint']['count']} processes.")
    eng = census.get("model_engines", {})
    if eng.get("count"):
        budget_gb = mem["total_gb"] * default_config()["model_budget_frac"]
        lines.append(f"Local model engines are holding {eng['rss_mb'] / 1024:.1f} GB "
                     f"of the {budget_gb:.1f} GB local-model budget.")
    leaked = [f for f in findings if f["rule"] == "leaked_test_browser"]
    if leaked:
        mb = sum(f["evidence"]["rss_mb"] for f in leaked)
        lines.append(f"{len(leaked)} leftover test browser{'s' if len(leaked) != 1 else ''} "
                     f"{'are' if len(leaked) != 1 else 'is'} holding {mb / 1024:.1f} GB.")
    for f in findings:
        if f["rule"] == "duplicate_singleton":
            lines.append(f"One duplicate helper is running ({f['singleton']}).")
        elif f["rule"] in ("orphaned_hog", "stale_dev_server", "model_over_budget"):
            lines.append(f["summary"] + " (needs your OK to touch)")
    for a in actions:
        if a["action"] == "term_leaked_browser" and a["ok"]:
            lines.append("Cleaned up a leftover test browser.")
        elif a["action"] == "dedupe_singleton" and a["ok"]:
            lines.append("Removed a duplicate helper.")
        elif a["action"] == "stop_idle_builtin_engine" and a["ok"]:
            lines.append("Stopped Xod's idle built-in model engine to free memory.")
    if not findings:
        lines.append("Nothing needs attention.")
    return lines


def _public_census(census: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in census.items() if k not in ("processes", "_cpu_seconds")}
    out["process_count"] = len(census["processes"])
    return out


def _append_log(entry: Dict[str, Any]) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning(f"health log write failed: {e}")


def _save_health(state: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    try:
        HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        history = state.get("history", [])[-cfg["history_keep"]:]
        payload = {"machine": os.uname().nodename if hasattr(os, "uname") else "this computer",
                   "updated_at": state["latest"]["at"], "latest": state["latest"], "history": history}
        tmp = HEALTH_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=1))
        tmp.replace(HEALTH_PATH)
    except Exception as e:
        logger.warning(f"health file write failed: {e}")


_state: Dict[str, Any] = {"latest": None, "history": [], "prev_census": None}


def status() -> Optional[Dict[str, Any]]:
    """The latest health state, from memory or from the health file (panel renders this)."""
    if _state.get("latest"):
        return {"latest": _state["latest"], "history": _state.get("history", [])[-12:]}
    try:
        return json.loads(HEALTH_PATH.read_text())
    except Exception:
        return None


def run_check(config: Optional[Dict[str, Any]] = None, do_actions: bool = True,
              light: bool = False) -> Dict[str, Any]:
    """One full check: census, detect, Tier 1 actions, verify with a fresh census,
    persist, log. Synchronous on purpose: the loop runs it in a thread."""
    cfg = config or default_config()
    before = collect_census(cfg, _state.get("prev_census"), light=light)
    findings = detect(before, cfg)
    actions = apply_tier1(before, findings) if do_actions else []
    after = before
    if actions:
        time.sleep(0.4)
        after = collect_census(cfg, before)
    readout = render_readout(before, findings, actions)
    latest = {"at": before["at"], "census": _public_census(before),
              "after": _public_census(after) if actions else None,
              "findings": findings, "actions": actions, "readout": readout}
    _state["latest"] = latest
    _state["history"] = (_state.get("history") or []) + [
        {"at": before["at"], "free_pct": before["memory"]["free_pct"],
         "findings": len(findings), "actions": len(actions)}]
    _state["prev_census"] = after
    _save_health(_state, cfg)
    _append_log({"at": before["at"], "kind": "check", "census": latest["census"],
                 "findings": findings, "actions": actions,
                 "after": latest["after"], "readout": readout})
    return latest


async def health_loop() -> None:
    """The always-on loop for the lightweight backend. Runs the sync check in a thread
    so the backend never blocks on a census. Adaptive: when the previous census showed
    memory pressure, the next check runs LIGHT (cheaper sampling) and the interval
    backs off x4, so the monitor itself never piles onto a melting machine."""
    cfg = default_config()
    logger.info(f"computer health: checking every {cfg['interval_sec']}s")
    while True:
        interval = cfg["interval_sec"]
        try:
            prev = _state.get("prev_census") or {}
            pressured = (prev.get("memory") or {}).get("free_pct", 100) < cfg["free_pct_floor"]
            latest = await asyncio.to_thread(run_check, cfg, True, pressured)
            extra = await pressure_relief(latest_census_full(), latest["findings"])
            if extra:
                _append_log({"at": time.time(), "kind": "pressure_relief", "actions": extra})
            try:
                # FIX C (services/model_budget.py): a memory-pressure census is the
                # pressure event: unload idle Xod-managed models across engines,
                # largest first. Models Xod did not load are never unloaded here;
                # they come back as suggestions for a Tier 2 approval. The same
                # pass reconciles the load ledger with what the engines hold.
                from app.services import model_budget
                res = await model_budget.model_budget.on_pressure(latest["findings"])
                if res and (res.get("actions") or res.get("suggestions")):
                    _append_log({"at": time.time(), "kind": "model_pressure_unload", **res})
                await model_budget.model_budget.reconcile()
            except Exception as e:
                logger.warning(f"model budget hook failed: {e}")
            if pressured:
                interval *= 4
        except Exception as e:
            logger.warning(f"computer health check failed: {e}")
        await asyncio.sleep(interval)


def latest_census_full() -> Dict[str, Any]:
    return _state.get("prev_census") or {}


def measure_own_cost() -> Dict[str, Any]:
    """What one check costs this process: wall time, RSS, and the budget verdict."""
    t0 = time.time()
    run_check(do_actions=False)
    dt = time.time() - t0
    rss = psutil.Process().memory_info().rss / 1024 ** 2
    return {"check_seconds": round(dt, 3), "process_rss_mb": round(rss, 1),
            "budget": OWN_BUDGET,
            "within_budget": dt <= OWN_BUDGET["census_seconds"] and rss <= OWN_BUDGET["rss_mb_ceiling"]}
