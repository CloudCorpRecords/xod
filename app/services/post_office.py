"""
A post office (Agent Mail relay) inside Xod: the same protocol as relay/server.py (relay/README.md), so this Xod can
host its own address with no shared post office. services/home_post.py runs it on its own port; it serves only these
endpoints, never the rest of Xod.

Differences from a public relay:
- Only this computer can claim or remove names (requests from 127.0.0.1 / ::1). Anyone who can reach it can look
  names up and drop off sealed mail, which is the point; they can't read anything.
- Each address answers to the domain it was set up with, so one Xod can be name@macbook.local:8790 on the home
  network or name@macbook.tail1234.ts.net:8790 on Tailscale.
"""
import base64
import json
import re
import sqlite3
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

MAX_ENVELOPE = 256 * 1024
MAILBOX_LIMIT = 500
EXPIRE_DAYS = 30
CLOCK_SKEW = 300
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,30}[a-z0-9])?$")
RESERVED = {"admin", "administrator", "root", "postmaster", "abuse", "support", "help", "security", "xod",
            "relay", "system", "noreply", "no-reply", "mail", "www", "api", "official", "team", "staff"}
LOCAL = {"127.0.0.1", "::1", "localhost"}


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _verify(sign_pub: str, data: bytes, sig: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(_b64d(sign_pub)).verify(_b64d(sig), data)
        return True
    except Exception:
        return False


class Registration(BaseModel):
    name: str
    sign_pub: str
    box_pub: str
    ts: int
    sig: str
    pair_code: str = ""   # a live "Pair a phone" code (services/phone_pair.py) is the one remote way in


class Post(BaseModel):
    to: str
    envelope: Dict[str, Any]


class Ack(BaseModel):
    ids: List[str]


def make_app(db_path: str, domain: Callable[[], str], pair_code_ok: Optional[Callable[[str], bool]] = None,
             on_mail: Optional[Callable[[str], None]] = None) -> FastAPI:
    """The post office. domain() is the domain addresses get at registration (this Xod's chosen network name).
    on_mail(name) is told each time mail is left for a name here (this Xod checks at once instead of at its next poll)."""
    app = FastAPI(title="Xod Post Office (this computer)", version="1", docs_url=None, redoc_url=None, openapi_url=None)
    lock = threading.Lock()
    hits: Dict[str, deque] = defaultdict(deque)

    def db() -> sqlite3.Connection:
        c = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS names (name TEXT PRIMARY KEY, sign_pub TEXT NOT NULL, box_pub TEXT NOT NULL,
                                          domain TEXT, registered_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS mail (id TEXT PRIMARY KEY, name TEXT NOT NULL, envelope TEXT NOT NULL,
                                         received_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS mail_name ON mail(name, received_at);
        """)

    def rate_limit(key: str, limit: int, per: float) -> None:
        now = time.time()
        q = hits[key]
        while q and now - q[0] > per:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, "Too many requests. Try again in a little while.")
        q.append(now)
        if len(hits) > 50000:
            hits.clear()

    def ip(request: Request) -> str:
        return request.client.host if request.client else "?"

    def only_here(request: Request) -> None:
        if ip(request) not in LOCAL:
            raise HTTPException(403, "Names on this post office are only claimed from the computer it runs on.")

    def name_row(name: str):
        with db() as c:
            return c.execute("SELECT * FROM names WHERE name=?", (name,)).fetchone()

    def authed(request: Request) -> str:
        name = (request.headers.get("x-xod-name") or "").lower()
        try:
            ts = int(request.headers.get("x-xod-time") or "0")
        except ValueError:
            raise HTTPException(401, "Bad signature time.")
        if abs(time.time() - ts) > CLOCK_SKEW:
            raise HTTPException(401, "Your clock is off, or this request is old.")
        row = name_row(name)
        msg = f"xod-relay|{request.method.upper()}|{request.url.path}|{name}|{ts}".encode()
        if not row or not _verify(row["sign_pub"], msg, request.headers.get("x-xod-sig") or ""):
            raise HTTPException(401, "Signature didn't match this name.")
        return name

    def addr(row) -> str:
        return f"{row['name']}@{row['domain'] or domain()}"

    @app.get("/.well-known/xod-relay")
    def about() -> Dict[str, Any]:
        return {"service": "xod-relay", "version": 1, "domain": domain(), "max_envelope_bytes": MAX_ENVELOPE,
                "expire_days": EXPIRE_DAYS, "self_hosted": True}

    @app.post("/v1/names")
    def register(reg: Registration, request: Request) -> Dict[str, Any]:
        # From this computer, or with a live one-time pairing code (phone_pair) that is
        # consumed only once the signed registration below has checked out.
        remote_pair = ip(request) not in LOCAL
        if remote_pair and not (reg.pair_code and pair_code_ok):
            only_here(request)
        name = reg.name.lower()
        if not NAME_RE.match(name) or name in RESERVED:
            raise HTTPException(400, "Names are 2-32 letters, numbers, dots, dashes or underscores, and some are reserved.")
        if abs(time.time() - reg.ts) > CLOCK_SKEW:
            raise HTTPException(400, "Your clock is off.")
        if not _verify(reg.sign_pub, f"xod-register|{name}|{reg.sign_pub}|{reg.box_pub}|{reg.ts}".encode(), reg.sig):
            raise HTTPException(400, "The registration isn't signed by that key.")
        if len(_b64d(reg.sign_pub)) != 32 or len(_b64d(reg.box_pub)) != 32:
            raise HTTPException(400, "Bad keys.")
        if remote_pair and not pair_code_ok(reg.pair_code):
            raise HTTPException(403, "That pairing code has expired or was already used. Ask Xod for a new QR.")
        now, dom = time.time(), domain()
        with lock, db() as c:
            row = c.execute("SELECT sign_pub FROM names WHERE name=?", (name,)).fetchone()
            if row and row["sign_pub"] != reg.sign_pub:
                raise HTTPException(409, f"{name}@{dom} is taken.")
            if row:
                c.execute("UPDATE names SET box_pub=?, domain=?, updated_at=? WHERE name=?", (reg.box_pub, dom, now, name))
            else:
                c.execute("INSERT INTO names VALUES (?,?,?,?,?,?)", (name, reg.sign_pub, reg.box_pub, dom, now, now))
        return {"address": f"{name}@{dom}", "name": name, "domain": dom}

    @app.get("/v1/names/{name}")
    def lookup(name: str, request: Request) -> Dict[str, Any]:
        rate_limit(f"look:{ip(request)}", 240, 60)
        row = name_row(name.lower())
        if not row:
            raise HTTPException(404, f"There's no {name}@{domain()}.")
        return {"name": row["name"], "address": addr(row), "sign_pub": row["sign_pub"], "box_pub": row["box_pub"],
                "registered_at": row["registered_at"]}

    @app.delete("/v1/names/{name}")
    def unregister(name: str, request: Request) -> Dict[str, Any]:
        only_here(request)
        who = authed(request)
        if who != name.lower():
            raise HTTPException(403, "You can only remove your own name.")
        with lock, db() as c:
            c.execute("DELETE FROM mail WHERE name=?", (who,))
            c.execute("DELETE FROM names WHERE name=?", (who,))
        return {"removed": True}

    @app.post("/v1/mail", status_code=202)
    def post_mail(post: Post, request: Request) -> Dict[str, Any]:
        rate_limit(f"post:{ip(request)}", 60, 60)
        name = post.to.split("@")[0].lower()
        rate_limit(f"to:{name}", 120, 60)
        raw = json.dumps(post.envelope, separators=(",", ":"))
        if len(raw) > MAX_ENVELOPE:
            raise HTTPException(413, "That message is too big.")
        if not all(k in post.envelope for k in ("eph", "nonce", "ct")):
            raise HTTPException(400, "Not a sealed envelope.")
        with lock, db() as c:
            if not c.execute("SELECT 1 FROM names WHERE name=?", (name,)).fetchone():
                raise HTTPException(404, f"There's no {name}@{domain()}.")
            if c.execute("SELECT COUNT(*) FROM mail WHERE name=?", (name,)).fetchone()[0] >= MAILBOX_LIMIT:
                raise HTTPException(507, "That mailbox is full.")
            mid = uuid.uuid4().hex
            c.execute("INSERT INTO mail VALUES (?,?,?,?)", (mid, name, raw, time.time()))
        if on_mail:
            try:
                on_mail(name)
            except Exception:
                pass
        return {"id": mid}

    @app.get("/v1/mail")
    def fetch_mail(request: Request) -> Dict[str, List[Dict[str, Any]]]:
        who = authed(request)
        rate_limit(f"fetch:{who}", 120, 60)
        with lock, db() as c:
            c.execute("DELETE FROM mail WHERE received_at < ?", (time.time() - EXPIRE_DAYS * 86400,))
            rows = c.execute("SELECT id, envelope, received_at FROM mail WHERE name=? ORDER BY received_at LIMIT 100", (who,)).fetchall()
        return {"messages": [{"id": r["id"], "envelope": json.loads(r["envelope"]), "received_at": r["received_at"]} for r in rows]}

    @app.post("/v1/mail/ack")
    def ack(body: Ack, request: Request) -> Dict[str, int]:
        who = authed(request)
        n = 0
        with lock, db() as c:
            for mid in body.ids[:200]:
                n += c.execute("DELETE FROM mail WHERE id=? AND name=?", (mid, who)).rowcount
        return {"deleted": n}

    return app
