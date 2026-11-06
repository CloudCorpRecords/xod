"""
Phase 3 of the simplicity redesign (Agent Mail becomes Family - Messages that
heal themselves):
  - the home post office restarts itself when down (stubbed home_post): the
    surface state stays silent while recovery is in progress and becomes
    "paused" only after the attempts genuinely fail; Fix it (repair) clears it
  - connection requests read in plain words: capability sentences derived
    from the shared set, via the real mail_words.js in node; raw JSON is a
    machine payload, never a surface preview
  - the inbox groups by sender (mail_words.js groupThreadsBySender)
  - safety numbers leave the Messages surface (source checks: no safety
    number in the header card; the contact safety number sits under Details)
Stubbed services + a throwaway XOD_DATA_DIR; no server, no real keychain.
  python tests/smoke/run_simplicity_phase3_test.py
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="xod-phase3-"))
os.environ["XOD_DATA_DIR"] = str(TMP)
os.environ.setdefault("XOD_SPEECH", "off")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


NODE_HARNESS = r"""
const MW = require(process.argv[2]);
const blob = JSON.stringify({ app_version: "test", capabilities: {
  apps: false, background_apps: false, calendar: false, files: false,
  messages: true, screen: false } });
const payload = MW.parseMachinePayload(blob);
const caps = MW.capabilitySet(payload);
const threads = [
  { thread: "t1", peers: ["dana@post.example.com"], count: 2, unread: 1, last_at: 100, last: { peer: "dana@post.example.com", subject: "A", text: "one" } },
  { thread: "t2", peers: ["dana@post.example.com"], count: 3, unread: 0, last_at: 50, last: { peer: "dana@post.example.com", subject: "B", text: "two" } },
  { thread: "t3", peers: ["sam@post.example.com"], count: 1, unread: 0, last_at: 200, last: { peer: "sam@post.example.com", subject: "C", text: "three" } },
];
const groups = MW.groupThreadsBySender(threads, "me@post.example.com");
console.log(JSON.stringify({
  can: MW.canSentence(caps), cannot: MW.cannotSentence(caps),
  sents: MW.capabilitySentences(caps),
  payloadIsMachine: MW.isMachinePayload(blob),
  helloIsMachine: MW.isMachinePayload("hello there"),
  capsFound: !!caps,
  previewJson: MW.plainPreview(blob, "hello"),
  previewPlain: MW.plainPreview("see you at 5", "Lunch?"),
  unknown: MW.capabilitySentences({ teleport: true }),
  groups: groups.map((g) => ({ key: g.key, n: g.threads.length, count: g.count, unread: g.unread, last_at: g.last_at })),
}));
"""


def main():
    print("H1 the post office heals itself (stubbed home_post)")
    from app.services import agent_mail, home_post

    agent_mail._save_config({"name": "rene", "domain": "mac.example:8790",
                             "address": "rene@mac.example:8790",
                             "home": {"domain": "mac.example:8790"}})
    state = {"running": False, "fail": True, "starts": 0}
    real = (home_post.running, home_post.start, home_post.stop)

    async def fake_start(dom):
        state["starts"] += 1
        if state["fail"]:
            return {"success": False, "error": "Port 8790 on this computer is in use by something else"}
        state["running"] = True
        return {"success": True, "domain": dom, "port": home_post.PORT}

    async def fake_stop():
        state["running"] = False

    home_post.running = lambda: state["running"]
    home_post.start = fake_start
    home_post.stop = fake_stop
    try:
        r1 = asyncio.run(agent_mail.ensure_home_post())
        check("first failure is silent recovery, not paused", r1["state"] == "recovering", str(r1))
        r2 = asyncio.run(agent_mail.ensure_home_post())
        check("second failure still recovering", r2["state"] == "recovering", str(r2))
        r3 = asyncio.run(agent_mail.ensure_home_post())
        check("third failure becomes paused", r3["state"] == "paused", str(r3))
        st = agent_mail.status()
        check("status reports paused (and keeps running/domain/port)",
              st["home"]["paused"] is True and st["home"]["running"] is False
              and "domain" in st["home"] and "port" in st["home"], str(st.get("home")))
        starts_at_pause = state["starts"]
        r4 = asyncio.run(agent_mail.ensure_home_post())
        check("while paused, no further automatic attempts", r4["state"] == "paused" and state["starts"] == starts_at_pause,
              str((r4, state["starts"])))
        state["fail"] = False
        rep = asyncio.run(agent_mail.repair_home_post())
        check("Fix it (repair) restarts the post office", rep.get("success") is True and state["running"], str(rep))
        check("status is calm again after repair", agent_mail.status()["home"]["paused"] is False)
        state["running"] = False
        state["fail"] = True
        r5 = asyncio.run(agent_mail.ensure_home_post())
        check("a later death starts a fresh recovery (attempt counter was reset)",
              r5["state"] == "recovering" and agent_mail._home_recovery["attempts"] == 1, str(r5))
        state["running"] = True
        r6 = asyncio.run(agent_mail.ensure_home_post())
        check("a running post office resets the recovery state",
              r6["state"] == "running" and agent_mail._home_recovery["attempts"] == 0, str(r6))
        agent_mail._save_config({"name": "rene", "domain": "relay.example", "address": "rene@relay.example"})
        r7 = asyncio.run(agent_mail.ensure_home_post())
        check("a shared post office has no home machinery", r7["state"] == "not_home", str(r7))
        rep2 = asyncio.run(agent_mail.repair_home_post())
        check("repair on a shared post office says so, invents nothing", rep2.get("success") is False, str(rep2))
    finally:
        home_post.running, home_post.start, home_post.stop = real
        agent_mail._home_recovery.update(attempts=0, paused=False, last_error=None)

    print("H2 plain-word requests + sender grouping (the real mail_words.js, in node)")
    node = shutil.which("node")
    check("node is available for the JS harness", bool(node))
    data = None
    if node:
        harness = TMP / "harness.js"
        harness.write_text(NODE_HARNESS, encoding="utf-8")
        proc = subprocess.run([node, str(harness), str(ROOT / "app" / "static" / "js" / "mail_words.js")],
                              capture_output=True, text=True, timeout=60)
        check("mail words harness runs", proc.returncode == 0, proc.stderr[:200])
        if proc.returncode == 0:
            data = json.loads(proc.stdout.strip().splitlines()[-1])
    if data:
        check("the spec's blob parses as a capability payload", data["capsFound"] and data["payloadIsMachine"])
        check("can sentence is the spec's sentence", data["can"] == "It can: send messages.", data["can"])
        check("cannot sentence names apps, calendar and files in words",
              data["cannot"].startswith("It cannot: ") and "use apps" in data["cannot"]
              and "see your calendar" in data["cannot"] and "read files" in data["cannot"], data["cannot"])
        check("no raw JSON or keys in the sentences",
              "{" not in data["can"] + data["cannot"] and "capabilities" not in data["can"] + data["cannot"])
        check("plain words a person wrote are not machine payloads", data["helloIsMachine"] is False)
        check("a JSON preview never shows JSON", "{" not in data["previewJson"] and "connect" in data["previewJson"], data["previewJson"])
        check("a plain preview still shows the subject", data["previewPlain"] == "Lunch?", data["previewPlain"])
        check("unknown capability keys are humanised, not shown raw", data["unknown"]["can"] == ["teleport"], str(data["unknown"]))
        g = data["groups"]
        check("threads group by sender, newest first", len(g) == 2 and g[0]["key"] == "sam@post.example.com", str(g))
        dana = next((x for x in g if x["key"] == "dana@post.example.com"), None)
        check("a sender group sums counts and keeps the latest",
              bool(dana) and dana["n"] == 2 and dana["count"] == 5 and dana["unread"] == 1 and dana["last_at"] == 100, str(dana))

    print("H3 the surface keeps its promises (source checks)")
    mail_js = (ROOT / "app" / "static" / "js" / "mail.js").read_text(encoding="utf-8")
    family_js = (ROOT / "app" / "static" / "js" / "family.js").read_text(encoding="utf-8")
    main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    mail_py = (ROOT / "app" / "services" / "agent_mail.py").read_text(encoding="utf-8")
    html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
    for bad in ["isn't running", "Restart Xod", "uses its port"]:
        check(f"the old banner text is gone: {bad!r}", bad not in mail_js)
    check("Messages shows the calm paused line + Fix it",
          "Messages are paused." in mail_js and 'data-ml="fix-post"' in mail_js)
    check("Fix it calls the repair endpoint", "/api/mail/home/repair" in mail_js and "/api/mail/home/repair" in main_py)
    header = mail_js.split("function headerCard")[1].split("function tabs")[0]
    check("no safety number in the Messages header card", "safety_number" not in header)
    check("the paused line is gated on the paused state, not on down alone", "home.paused" in header)
    contact_card = mail_js.split("data-ml-contact")[1].split("autoreply")[0]
    check("contact safety number moved under Details",
          "<details" in contact_card and "${esc(c.safety)}" in contact_card
          and contact_card.index("<details") < contact_card.index("${esc(c.safety)}"))
    check("the inbox groups by sender", "groupThreadsBySender" in mail_js and "data-ml-sender" in mail_js)
    check("requests render as plain-word cards with Allow",
          "wants to connect." in mail_js and ">Allow</button>" in mail_js and "canSentence" in mail_js)
    check("Family shows the paused line too", "Messages are paused." in family_js and "data-family-fix" in family_js)
    check("Family surfaces request cards with Allow/Block",
          "Requests waiting" in family_js and "data-family-request-action" in family_js
          and "/api/mail/threads?box=requests" in family_js)
    check("the mail loop heals the post office every cycle", "await ensure_home_post()" in mail_py)
    check("mail_words.js is loaded by the page", "mail_words.js" in html)

    failed = [n for n, ok in results if not ok]
    print(f"SUMMARY {'PASS' if not failed else 'FAIL'} {len(results) - len(failed)}/{len(results)} checks passed"
          + (f" — failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
