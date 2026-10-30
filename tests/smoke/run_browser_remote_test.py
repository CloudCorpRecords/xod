"""
Take Over from the phone (services/browser_remote.py, phone_api browser.*): Xod's headless browser on a local sign-in
page. Xod notices the sign-in wall, the phone gets a picture that fits one Agent Mail message, a tap focuses the box
the picture shows, typing lands in the page, and what was typed isn't kept on the computer or in the browser's
history. Uses a throwaway data folder and its own browser port; needs Chrome.
  .venv/bin/python tests/smoke/run_browser_remote_test.py
"""
import asyncio
import base64
import json
import os
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.update({"XOD_DATA_DIR": tempfile.mkdtemp(prefix="xod-takeover-"), "XOD_NO_UPDATE_CHECK": "1",
                   "XOD_BROWSER_PORT": "9466"})
sys.path.insert(0, str(ROOT))
results = []

PAGE = b"""<!doctype html><title>Sign in to Example</title>
<style>body{margin:0;font:20px sans-serif} input{position:absolute;left:100px;width:400px;height:40px}</style>
<h1 style="margin:20px">Sign in</h1>
<input id=user style="top:200px" placeholder=Email>
<input id=pw type=password style="top:300px" placeholder=Password>"""


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


async def main():
    from app.services import browser_remote as br, phone_api
    from app.services.browser_sandbox import browser_sandbox_service as sb
    from app.agent import tools
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = HTTPServer(("127.0.0.1", port), Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/login"
    try:
        print("Sign-in wall")
        r = await tools._execute_tool("browser_navigate", {"url": url})
        check("navigating to a sign-in page with no saved login says so", "sign_in_needed" in r and "phone" in r["sign_in_needed"],
              json.dumps(r)[:300])
        check("…and never asks for the password in chat", "Never ask for their password" in r.get("sign_in_needed", ""))
        check("the chat answer to the phone carries the site", (br.signin_since(0) or {}).get("site") == f"127.0.0.1:{port}")

        print("The picture")
        shot = await br.screen()
        img = br.to_image_bytes(shot)
        check("a JPEG of the page", img[:3] == b"\xff\xd8\xff", str(img[:4]))
        check("sized for the phone (720 across)", shot["width"] <= 720 and shot["height"] > 0, f"{shot['width']}x{shot['height']}")
        check("small enough for one message", len(shot["image"]) <= br.MAX_IMAGE, str(len(shot["image"])))
        check("with the page's address and title", shot["url"].startswith(url) and shot["title"] == "Sign in to Example", shot["url"])
        reply = phone_api.encode("r1", {"ok": True, "data": shot}, phone_api.MAX_SCREEN_REPLY)
        check("the whole answer fits a screen reply", len(reply) <= phone_api.MAX_SCREEN_REPLY and json.loads(reply)["ok"])

        print("Tap and type")
        pw, pw_y = "hunter2-secret", (300 + 20) / shot["page_height"]
        await br.tap(300 / shot["page_width"], pw_y)
        focused = (await sb.evaluate_js("document.activeElement && document.activeElement.id")).get("result")
        check("a tap where the picture shows the password box focuses it", focused == "pw", str(focused))
        after = await br.type_text(pw)
        value = (await sb.evaluate_js("document.getElementById('pw').value")).get("result")
        check("typing lands in the page", value == pw, str(value))
        check("…and comes back with a fresh picture", bool(after.get("image")))
        check("what was typed isn't in the browser's history (the AI reads that)",
              pw not in json.dumps(sb.action_history, default=str))

        print("Keys, scroll, back")
        await br.key("backspace")
        value = (await sb.evaluate_js("document.getElementById('pw').value")).get("result")
        check("backspace", value == pw[:-1], str(value))
        try:
            await br.key("f5")
            check("unknown keys are refused", False)
        except ValueError:
            check("unknown keys are refused", True)
        try:
            await br.go("javascript:alert(1)")
            check("only web addresses can be opened", False)
        except ValueError:
            check("only web addresses can be opened", True)
        check("scrolling answers with a picture", bool((await br.scroll("down")).get("image")))

        print("Done")
        msg = br.done()["message"]
        check("finishing thanks them and clears the sign-in", "session" in msg and br.signin_since(0) is None, msg)
    finally:
        srv.shutdown()
        sb.close()

    print("Phone requests")
    from app.services import agent_mail
    sent, stored = [], {}

    async def fake_send(to, text, subject="", kind="message", thread=None, in_reply_to=None, msg_id=None, origin="you", max_text=20000):
        sent.append({"text": text, "max_text": max_text})
        return {"thread": thread}
    agent_mail.send = fake_send
    phone_api.allowed = lambda sender: True
    with agent_mail._db() as c:
        c.execute("INSERT INTO mail_messages (id, msg_id, thread, direction, peer, recipients, kind, subject, text, status, "
                  "verified, created_at, read) VALUES ('in-1','m1','t1','in','phone@x','[]','message','xod api',?,'new',1,0,0)",
                  (json.dumps({"op": "browser.type", "request_id": "r9", "args": {"text": "my-password"}}),))

    async def typed(args, what):
        return {"typed": True}
    phone_api.OPS["browser.type"] = lambda a: typed(a, "type")
    await phone_api.handle("phone@x", {"text": json.dumps({"op": "browser.type", "request_id": "r9", "args": {"text": "my-password"}}),
                                       "thread": "t1", "id": "m1"}, "in-1")
    with agent_mail._db() as c:
        kept = c.execute("SELECT text FROM mail_messages WHERE id='in-1'").fetchone()[0]
    check("a typed password isn't kept in the computer's mail", "my-password" not in kept and "(hidden)" in kept, kept)
    check("its answer may be a screen-sized message", sent and sent[-1]["max_text"] == phone_api.MAX_SCREEN_REPLY)


asyncio.run(main())
failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
