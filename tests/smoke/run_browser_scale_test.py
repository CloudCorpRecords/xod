"""
Scale & Fleet Verification Suite (2026-11-24):
  Part A: MCP server steerer tool surface (browser_identity, browser_adopt, browser_detach,
          browser_tabs, browser_run, browser_sessions)
  Part B: Live concurrent 2-browser pool (2 independent Chrome instances running simultaneously
          on distinct CDP ports & profiles, zero contention, all steps verified)
  Part C: Long-form context integration during browser runs (extract -> longform.db -> budgeted recall)
  Part D: Open-source Google Drive shared memory template (conflict-free multi-device shard sync)

Runs completely isolated (own XOD_DATA_DIR, never touches port 9228 or the running 1.1.21 app).
"""
import ast
import asyncio
import http.server
import os
import socketserver
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_bscale_"))
os.environ.update({
    "XOD_DATA_DIR": str(TMP / "data"),
    "XOD_NO_UPDATE_CHECK": "1",
    "XOD_SPEECH": "off",
    "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
    "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"),
    "XOD_SECRETS_SERVICE": "Xod test keys",
})
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

from app import paths
paths.DEV_ENV_PATH = TMP / "absent.env"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


# ================================================================ Part A: MCP Server Tools
def part_a_mcp_tools():
    src = (ROOT / "app" / "mcp_server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn_names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    required = {"browser_identity", "browser_adopt", "browser_detach", "browser_tabs", "browser_run", "browser_sessions"}
    missing = required - fn_names
    check("A1 app/mcp_server.py compiles cleanly and exposes all 6 browser steerer tools", not missing, f"missing={missing}")


# ================================================================ Part B & C: Live 2-Browser Pool + Long-Form Context
LONG_FILLER = ("Background engineering notes about distributed browser orchestration and reliable telemetry. " * 25) + "\n\n"

PAGES = {
    "/alpha": f"""<!doctype html><html><head><title>Alpha Station</title></head><body>
      <h1>Alpha Station</h1>
      <p>{LONG_FILLER}</p>
      <p>The quantum turbine serial number is QT-9942-ALPHA.</p>
      <p>{LONG_FILLER}</p>
      <input id="a-in" type="text" />
      <button id="a-btn" onclick="document.getElementById('a-out').textContent='alpha-saved:' + document.getElementById('a-in').value">Save Alpha</button>
      <div id="a-out"></div>
    </body></html>""",
    "/beta": f"""<!doctype html><html><head><title>Beta Station</title></head><body>
      <h1>Beta Station</h1>
      <p>{LONG_FILLER}</p>
      <p>The solar inverter calibration code is SI-7731-BETA.</p>
      <p>{LONG_FILLER}</p>
      <input id="b-in" type="text" />
      <button id="b-btn" onclick="document.getElementById('b-out').textContent='beta-saved:' + document.getElementById('b-in').value">Save Beta</button>
      <div id="b-out"></div>
    </body></html>""",
}


class ScaleHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = PAGES.get(self.path, PAGES["/alpha"]).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


async def part_bc_parallel_and_longform():
    from app.services.browser_pool import BrowserPoolService
    from app.services import longform_context

    httpd = socketserver.TCPServer(("127.0.0.1", 0), ScaleHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    pool = BrowserPoolService(base_data_dir=TMP / "pool_profiles")
    lf_session = "scale-parallel-test"
    try:
        jobs = [
            {
                "session_id": "browser_alpha",
                "longform_session": lf_session,
                "steps": [
                    {"action": "navigate", "url": f"{base}/alpha", "expect": {"title_contains": "Alpha"}},
                    {"action": "type", "selector": "#a-in", "text": "alpha-val"},
                    {"action": "click", "target": "#a-btn", "expect": {"text_contains": "alpha-saved:alpha-val"}},
                    {"action": "extract", "name": "alpha_page"},
                ],
            },
            {
                "session_id": "browser_beta",
                "longform_session": lf_session,
                "steps": [
                    {"action": "navigate", "url": f"{base}/beta", "expect": {"title_contains": "Beta"}},
                    {"action": "type", "selector": "#b-in", "text": "beta-val"},
                    {"action": "click", "target": "#b-btn", "expect": {"text_contains": "beta-saved:beta-val"}},
                    {"action": "extract", "name": "beta_page"},
                ],
            },
        ]
        res = await pool.run_parallel(jobs, close_after=False)
        outcomes = res.get("outcomes") or []
        ports = {o.get("port") for o in outcomes}

        check("B1 2 independent browsers completed all steps in parallel", res.get("success") is True and len(outcomes) == 2, str(res.get("outcomes")))
        check("B2 each browser ran on its own distinct CDP port", len(ports) == 2 and 9228 not in ports, f"ports={ports}")
        check("B3 concurrency ratio > 1.2x (true parallel execution)", float(res.get("concurrency_ratio", 0)) > 1.2, f"wall={res.get('wall_sec')}s sum={res.get('sum_job_sec')}s ratio={res.get('concurrency_ratio')}")

        alpha_ext = (outcomes[0].get("result") or {}).get("extracts", {}).get("alpha_page", "")
        beta_ext = (outcomes[1].get("result") or {}).get("extracts", {}).get("beta_page", "")
        check("B4 browser_alpha and browser_beta captured distinct page states without cross-contamination",
              "QT-9942-ALPHA" in alpha_ext and "SI-7731-BETA" in beta_ext and "SI-7731-BETA" not in alpha_ext,
              f"alpha_len={len(alpha_ext)} beta_len={len(beta_ext)}")

        # Part C: Long-form recall from the extracts ingested during the parallel browser runs
        recalled_a = longform_context.recall("quantum turbine serial number", budget_tokens=400, session_id=lf_session)
        recalled_b = longform_context.recall("solar inverter calibration code", budget_tokens=400, session_id=lf_session)
        check("C1 long-form recall retrieves QT-9942-ALPHA within 400-token budget",
              "QT-9942-ALPHA" in (recalled_a.get("text") or "") and int(recalled_a.get("tokens_used", 999)) <= 400,
              f"tokens={recalled_a.get('tokens_used')} text={str(recalled_a.get('text'))[:120]}")
        check("C2 long-form recall retrieves SI-7731-BETA within 400-token budget",
              "SI-7731-BETA" in (recalled_b.get("text") or "") and int(recalled_b.get("tokens_used", 999)) <= 400,
              f"tokens={recalled_b.get('tokens_used')} text={str(recalled_b.get('text'))[:120]}")

        # Also test recall step directly inside run_browser_steps
        sb_a = pool.get_or_create("browser_alpha")
        from app.services.browser_run import run_browser_steps
        rec_step = await run_browser_steps(
            sb_a,
            [{"action": "recall", "query": "quantum turbine", "budget": 350, "name": "found_fact"}],
            longform_session=lf_session,
        )
        check("C3 browser_run 'recall' step lands and returns budgeted fact",
              rec_step.get("status") == "completed" and "QT-9942-ALPHA" in (rec_step.get("extracts", {}).get("found_fact") or ""),
              str(rec_step.get("status")))
    finally:
        closed = pool.close_all()
        check("B5 pool.close_all() cleanly closed both browser sessions", len(closed.get("closed") or []) == 2 and len(pool.list_sessions()) == 0, str(closed))
        httpd.shutdown()


# ================================================================ Part D: Google Drive Shared Memory Template
def part_d_drive_template():
    from app.services.memory_drive_template import DriveSharedMemoryAdapter

    drive_dir = TMP / "MyDrive_XodSharedMemory"
    dev_a = DriveSharedMemoryAdapter(shared_dir=drive_dir, device_id="macbook-darwin")
    dev_b = DriveSharedMemoryAdapter(shared_dir=drive_dir, device_id="desktop-windows")

    init_res = dev_a.init_template()
    check("D1 Drive shared memory template initializes manifest & README",
          init_res.get("success") is True and (drive_dir / "manifest.json").exists() and (drive_dir / "README.txt").exists(),
          str(init_res))

    exp_a = dev_a.export_items([
        {"kind": "fact", "category": "workflow", "content": "User prefers branch-first PRs with destination read-back"},
        {"kind": "fact", "category": "fleet", "content": "MacBook runs Xod 1.1.21 on Tailscale"},
    ])
    exp_a_dup = dev_a.export_items([
        {"kind": "fact", "category": "workflow", "content": "User prefers branch-first PRs with destination read-back"},
    ])
    check("D2 Device A export writes 2 facts and deduplicates repeat export (written=0)",
          exp_a.get("written") == 2 and exp_a_dup.get("written") == 0,
          f"first={exp_a} dup={exp_a_dup}")

    imp_b = dev_b.import_peer_items()
    check("D3 Device B imports both facts from Device A shard",
          imp_b.get("count") == 2 and "macbook-darwin" in (imp_b.get("peers") or []),
          str(imp_b))

    dev_b.export_items([{"kind": "fact", "category": "device", "content": "Windows desktop has Muse extension UIA tree"}])
    imp_a = dev_a.import_peer_items()
    check("D4 Device A imports Device B shard with separate conflict-free JSONL files",
          imp_a.get("count") == 1 and (drive_dir / "devices" / "macbook-darwin.jsonl").exists() and (drive_dir / "devices" / "desktop-windows.jsonl").exists(),
          str(imp_a))


async def main():
    part_a_mcp_tools()
    await part_bc_parallel_and_longform()
    part_d_drive_template()
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\n{passed}/{total} checks passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
