"""
A paired phone's ways home (home_post.routes(), phone_pair.routes(), phone_api "link.routes"):
  - this computer's own post office lists its .local name and LAN address, then Tailscale's name and address when on
  - XOD_POST_ROUTES replaces what's found
  - whether any route works away from home (Tailscale or https), so the screens can say so
  - the pairing QR carries them all, its own post office first; a shared relay has just the one
  - the phone app can ask for them over mail (for phones paired before routes existed)
No network, no Xod running: Tailscale and Agent Mail's settings are stood in for.
  .venv/bin/python tests/smoke/run_phone_routes_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("XOD_DATA_DIR", tempfile.mkdtemp(prefix="xod-routes-"))
os.environ.pop("XOD_POST_ROUTES", None)

from app.services import agent_mail, home_post, phone_api, phone_pair  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def main():
    P = home_post.PORT
    home_post.local_name = lambda: "home-pc"
    home_post.lan_ip = lambda: "192.168.1.20"
    home_post.tailscale = lambda: None
    r = home_post.routes()
    check("home network routes", r == [f"http://home-pc.local:{P}", f"http://192.168.1.20:{P}"], r)
    check("no Tailscale: phones work on this Wi-Fi only", not home_post.reaches_away(r))

    home_post.tailscale = lambda: {"name": "home-pc.tail1234.ts.net", "ip": "100.101.1.2"}
    r = home_post.routes()
    check("Tailscale's name and address follow", r[2:] == [f"http://home-pc.tail1234.ts.net:{P}", f"http://100.101.1.2:{P}"], r)
    check("with Tailscale: works away from home", home_post.reaches_away(r))
    check("a Tailscale address alone counts", home_post.reaches_away([f"http://100.101.1.2:{P}"]))
    check("https counts", home_post.reaches_away(["https://post.example.com"]))
    check("a LAN address doesn't", not home_post.reaches_away([f"http://10.0.0.5:{P}"]))

    os.environ["XOD_POST_ROUTES"] = "https://home.example.com/, http://home-pc.local:9000"
    check("XOD_POST_ROUTES replaces them", home_post.routes() == ["https://home.example.com", "http://home-pc.local:9000"])
    os.environ.pop("XOD_POST_ROUTES")

    # Hosting its own post office: the QR's post office first (Tailscale's name when on), then every other route.
    cfg = {"home": {"domain": f"home-pc.local:{P}"}, "domain": f"home-pc.local:{P}", "address": f"you@home-pc.local:{P}"}
    agent_mail._config = lambda: cfg
    agent_mail.status = lambda: {"set_up": True, "address": cfg["address"], "safety_number": "12345 67890"}
    pr = phone_pair.routes()
    check("phone_pair: post office first, no repeats", pr[0] == f"http://home-pc.tail1234.ts.net:{P}" and len(pr) == len(set(pr)) == 4, pr)
    offer = phone_pair.create_offer()
    payload = json.loads(offer["qr_text"])
    check("the QR carries the routes", payload.get("routes") == pr and payload["post_office"] == pr[0], payload)
    check("the desktop screen hears it works away", offer.get("away") is True and offer.get("tailscale") is True)
    phone_pair.cancel()

    out = asyncio.run(phone_api.run("link.routes", {}))
    check("the phone app can ask for them", out.get("ok") and out["data"]["routes"] == pr and out["data"]["away"] is True, out)

    # A shared relay: one name, no extra routes.
    cfg.update(home=None, domain="post.example.com", address="you@post.example.com")
    check("a shared relay has just its one", phone_pair.routes() == ["https://post.example.com"], phone_pair.routes())

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
