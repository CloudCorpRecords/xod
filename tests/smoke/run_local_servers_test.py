"""
A saved API key instead of a local model, and knowing what local model is running (services/local_servers.py,
setup_wizard.prefer_key):
  - a new install with an API key saved answers with that key; no local model needed
  - a model you picked yourself is never swapped for the key; neither is local-first routing (cloud for hard steps)
  - switching to the key stops the built-in engine at once (its memory is freed)
  - the built-in engine stops itself after the idle time, but not while it's working, and never with "never"
  - the header's report: what answers chats, which local servers run, what they hold, and what's loaded but unused
The built-in engine is stood in for by a plain process (no model download). Throwaway data folder and keyring;
Ollama is pointed at a closed port. LM Studio, if it runs on this computer, is only read, never unloaded.
  .venv/bin/python tests/smoke/run_local_servers_test.py
"""
import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-localservers-"))


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


os.environ.update({"XOD_DATA_DIR": str(TMP / "data"), "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring",
                   "XOD_TEST_KEYRING_FILE": str(TMP / "keys.json"), "XOD_NO_UPDATE_CHECK": "1",
                   "OLLAMA_HOST": f"127.0.0.1:{_closed_port()}"})
for k in ("LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "XOD_ENGINE_IDLE_MINUTES", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
          "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "FIREWORKS_API_KEY"):
    os.environ.pop(k, None)
(TMP / "data").mkdir(parents=True)
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def fake_engine(busy: bool = False) -> subprocess.Popen:
    code = "while True: pass" if busy else "import time; time.sleep(120)"
    return subprocess.Popen([sys.executable, "-c", code])


async def main() -> None:
    from app.config import settings
    from app.main import update_config, ConfigUpdateRequest
    from app.services import local_servers, setup_wizard, llm_routing
    from app.services.local_engine import local_engine
    from app.services.storage import set_setting, get_setting

    print("L1 a new install, no key")
    r = await local_servers.report()
    check("the report answers, with the built-in engine off", r["servers"][0]["id"] == "builtin" and not r["servers"][0]["running"], str(r["servers"][0]))
    check("…and Ollama (closed port) not running", next(s for s in r["servers"] if s["id"] == "ollama")["running"] is False)
    check("nothing to switch to without a key", setup_wizard.prefer_key()["switched"] is False)

    print("L2 an API key saved: it wins, and the built-in engine stops")
    local_engine.proc, local_engine.repo, local_engine.started_at = fake_engine(), "mlx-community/test-model", time.time()
    local_engine.last_used = time.time()
    pid = local_engine.proc.pid
    res = await update_config(ConfigUpdateRequest(anthropic_api_key="sk-test-not-a-real-key"))
    check("saving the key switches Xod to it", res.get("switched_to") == "anthropic" and settings.llm_provider == "anthropic",
          f"{res} provider={settings.llm_provider}")
    check("…as Xod's own pick (so a key you add later can still take over)", setup_wizard.chosen_by() == "auto")
    check("…and says so", "API key" in res.get("message", ""), res.get("message"))
    for _ in range(40):
        if not local_engine.running():
            break
        await asyncio.sleep(0.25)
    check("the built-in engine was stopped by the switch", not local_engine.running())
    import psutil
    check("…its process is gone", not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE)
    r = await local_servers.report()
    check("the report says chats are answered online", r["answering"]["provider"] == "anthropic" and r["answering"]["where"] == "online"
          and r["answering"]["ready"] is True, str(r["answering"]))
    status = setup_wizard.saved_keys()
    check("setup lists the saved key first", status[:1] == ["anthropic"], str(status))
    s = await setup_wizard.status()
    check("…with a model ready to pick", s["cloud"] and s["cloud"][0]["provider"] == "anthropic" and s["cloud"][0]["model"], str(s.get("cloud")))

    print("L3 a model you picked yourself is never replaced")
    pick = setup_wizard.choose("ollama", "llama3.1", how="you")
    check("you pick a local model", pick.get("success") and settings.llm_provider == "ollama", str(pick))
    res = await update_config(ConfigUpdateRequest(openai_api_key="sk-test-other-key"))
    check("saving another key leaves your pick alone", settings.llm_provider == "ollama" and not res.get("switched_to"), f"{res} {settings.llm_provider}")

    print("L4 local-first routing (cloud only for hard steps) is left alone")
    setup_wizard.choose("ollama", "llama3.1", how="auto")
    llm_routing.save_settings(mode="ask")
    check("no switch while routing is on", setup_wizard.prefer_key()["switched"] is False and settings.llm_provider == "ollama")
    llm_routing.save_settings(mode="off")
    check("with routing off, Xod's own local pick gives way to the key", setup_wizard.prefer_key()["switched"] is True
          and settings.llm_provider == "anthropic")

    print("L5 the built-in engine stops itself when unused")
    set_setting("engine_idle_minutes", "15")
    local_engine.proc, local_engine.repo, local_engine.started_at = fake_engine(), "mlx-community/test-model", time.time()
    local_engine.last_used = time.time() - 5 * 60
    check("not before the idle time", await local_servers.check_idle() is None and local_engine.running())
    r = await local_servers.report()
    b = r["servers"][0]
    check("the report shows it running, with its memory and idle time", b["running"] and b["memory_gb"] is not None and b["idle_seconds"] >= 290, str(b))
    check("…and flags it as loaded but unused (chats use the key)", "builtin" in r["unused"], str(r["unused"]))
    local_engine.last_used = time.time() - 20 * 60
    msg = await local_servers.check_idle()
    check("after the idle time it stops", msg and not local_engine.running(), str(msg))

    if sys.platform != "win32":       # processor use reads unreliably on Windows runners; the built-in engine is Mac only
        local_engine.proc, local_engine.started_at = fake_engine(busy=True), time.time()
        local_engine.last_used = time.time() - 20 * 60
        await asyncio.sleep(0.5)
        check("…but not while it's working on something", await local_servers.check_idle() is None and local_engine.running())
        await local_engine.stop()

    set_setting("engine_idle_minutes", "0")
    local_engine.proc, local_engine.started_at = fake_engine(), time.time()
    local_engine.last_used = time.time() - 600 * 60
    check("\"never\" keeps it running", await local_servers.check_idle() is None and local_engine.running())

    print("L6 Stop from the header")
    res = await local_servers.stop("builtin")
    check("Stop stops the built-in engine", res["success"] and res["stopped"] and not local_engine.running(), str(res))
    res = await local_servers.stop("builtin")
    check("…and says so when it's already off", res["success"] and not res["stopped"])
    check("an unknown server is refused", (await local_servers.stop("nope"))["success"] is False)
    set_setting("engine_idle_minutes", "")
    check("the idle time defaults to 15 minutes", local_servers.idle_minutes() == 15)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        try:
            from app.services.local_engine import local_engine
            if local_engine.proc and local_engine.proc.poll() is None:
                local_engine.proc.kill()
        except Exception:
            pass
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)
