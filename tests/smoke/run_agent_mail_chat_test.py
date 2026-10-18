"""
"Set up agent mail" in the chat (agent/tools.py agent_mail_setup, agent/operator.py): it goes to Agent Mail, not the
Mail app; the add-on turns on; with a shared relay it claims the address there; mail between two Xods arrives. (With no
relay, Xod hosts its own post office: tests/smoke/run_home_post_test.py.) A relay of its own on this computer (relay/server.py), a throwaway keyring; no network.
  .venv/bin/python tests/smoke/run_agent_mail_chat_test.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod-agentmail-"))
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def as_xod(who: str, code: str) -> dict:
    """Runs code as one Xod (its own data folder and keys); it prints one JSON line."""
    (TMP / who).mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "XOD_DATA_DIR": str(TMP / who), "PYTHONUTF8": "1",
           "PYTHON_KEYRING_BACKEND": "tests.support.filekeyring.FileKeyring", "XOD_TEST_KEYRING_FILE": str(TMP / f"{who}-keys.json")}
    out = subprocess.run([sys.executable, "-c", "import asyncio, json\nfrom app.agent import tools, operator\n"
                          "from app.services import agent_mail, packs\n" + code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    line = next((l for l in reversed(out.stdout.splitlines()) if l.startswith("{")), "")
    if not line:
        print(out.stdout[-800:], out.stderr[-1500:])
    return json.loads(line or "{}")


def main():
    port = free_port()
    relay = f"localhost:{port}"
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=ROOT / "relay", env={**os.environ, "XOD_RELAY_DOMAIN": relay, "XOD_RELAY_DB": str(TMP / "relay.db")},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://{relay}/.well-known/xod-relay", timeout=1)
                break
            except Exception:
                time.sleep(0.2)

        print("M1 the request goes to Agent Mail")
        r = as_xod("rene", """
q = 'can you set up agent mail'
names = [t['function']['name'] for t in tools.select_tools_for_query(q)]
print(json.dumps({'routed': operator.route(q), 'tools': names}))""")
        check("'set up agent mail' isn't sent to the Mail app", r.get("routed") is None, str(r.get("routed"))[:200])
        check("…and the chat gets the setup tool", "agent_mail_setup" in r.get("tools", []), str(r.get("tools")))

        print("M2 setting it up")
        r = as_xod("rene", """
before = 'agent-mail' in packs.enabled()
print(json.dumps({'before': before, 'after': (packs.set_enabled('agent-mail', True) or True) and 'agent-mail' in packs.enabled()}))""")
        check("the add-on turns on", r.get("before") is False and r.get("after") is True, str(r))
        # (with no post office named, the setup hosts on this computer: tests/smoke/run_home_post_test.py)
        r = as_xod("rene", f"""
r = asyncio.run(tools.execute_tool('agent_mail_setup', {{'name': 'rene', 'relay': '{relay}'}}))
print(json.dumps(r))""")
        check("with a relay, it claims the address", r.get("success") and r.get("address") == f"rene@{relay}", str(r)[:200])
        r = as_xod("rene", "print(json.dumps(asyncio.run(tools.execute_tool('agent_mail_setup', {}))))")
        check("asked again, it says it's already set up", r.get("already") and r.get("address") == f"rene@{relay}", str(r)[:200])
        r = as_xod("dana", f"print(json.dumps(asyncio.run(tools.execute_tool('agent_mail_setup', {{'name': 'rene', 'relay': '{relay}'}}))))")
        check("someone else can't take a name that's claimed", not r.get("success"), str(r)[:200])

        print("M3 mail between two Xods")
        as_xod("dana", f"print(json.dumps(asyncio.run(tools.execute_tool('agent_mail_setup', {{'name': 'dana', 'relay': '{relay}'}}))))")
        r = as_xod("dana", f"print(json.dumps(asyncio.run(agent_mail.send('rene@{relay}', 'Hi Rene, can your agent send me the report?'))))")
        check("dana's Xod sends", r.get("success"), str(r)[:200])
        r = as_xod("rene", "print(json.dumps(asyncio.run(tools.execute_tool('agent_mail_inbox', {}))))")
        msgs = r.get("messages") or []
        check("it arrives in Requests (dana isn't a contact yet), marked as from another agent",
              any(m["box"] == "requests" and m["from"] == f"dana@{relay}" and "the report" in m["message"] for m in msgs), str(msgs)[:300])
    finally:
        srv.terminate()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
