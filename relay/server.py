"""
Xod Post Office: the relay that carries Agent Mail between Xods.

It's deliberately dumb. It keeps a directory of names (name -> the agent's public keys, claimed
with a signature) and holds sealed envelopes until their recipient picks them up, then deletes
them. It never sees who sent a message or what it says: envelopes are end-to-end encrypted and
the sender is inside the seal. Undelivered mail expires after 30 days.

  pip install fastapi uvicorn cryptography
  XOD_RELAY_DOMAIN=post.example.com uvicorn server:app --host 0.0.0.0 --port 8790

Addresses are name@<XOD_RELAY_DOMAIN>. Put it behind HTTPS (Caddy, Cloudflare, Fly…).
Protocol: see README.md. The Cloudflare Worker in worker/ speaks the same protocol.
"""
import base64
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

DOMAIN = os.environ.get("XOD_RELAY_DOMAIN", "localhost:8790")
DB_PATH = os.environ.get("XOD_RELAY_DB", "relay.db")
MAX_ENVELOPE = int(os.environ.get("XOD_RELAY_MAX_BYTES", 256 * 1024))
MAILBOX_LIMIT = 500                 # undelivered messages held per name
EXPIRE_DAYS = 30
CLOCK_SKEW = 300
# Self-host directory liveness: self-hosts heartbeat every 5 minutes; a record with no
# heartbeat for 3 intervals (15 minutes) is served as stale, and records unseen for a day
# are dropped. DIRECTORY_STALE_AFTER is overridable for tests.
DIRECTORY_HEARTBEAT = 300
DIRECTORY_STALE_AFTER = int(os.environ.get("XOD_RELAY_DIR_STALE", 3 * DIRECTORY_HEARTBEAT))
DIRECTORY_EXPIRE_AFTER = 24 * 3600
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,30}[a-z0-9])?$")
RESERVED = {"admin", "administrator", "root", "postmaster", "abuse", "support", "help", "security", "xod",
            "relay", "system", "noreply", "no-reply", "mail", "www", "api", "official", "team", "staff"}

app = FastAPI(title="Xod Post Office", version="1")
_lock = threading.Lock()


def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


with db() as _c:
    _c.executescript("""
    CREATE TABLE IF NOT EXISTS names (name TEXT PRIMARY KEY, sign_pub TEXT NOT NULL, box_pub TEXT NOT NULL,
                                      registered_at REAL NOT NULL, updated_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS mail (id TEXT PRIMARY KEY, name TEXT NOT NULL, envelope TEXT NOT NULL,
                                     received_at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS mail_name ON mail(name, received_at);
    CREATE TABLE IF NOT EXISTS directory (address TEXT PRIMARY KEY, endpoint TEXT NOT NULL,
                                          sign_pub TEXT NOT NULL, box_pub TEXT NOT NULL, status TEXT NOT NULL,
                                          challenge_id TEXT, challenge TEXT,
                                          registered_at REAL NOT NULL, last_seen REAL NOT NULL);
    """)


# ---------------------------------------------------------------- helpers

def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify(sign_pub: str, data: bytes, sig: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(b64d(sign_pub)).verify(b64d(sig), data)
        return True
    except Exception:
        return False


_hits: Dict[str, deque] = defaultdict(deque)


def rate_limit(key: str, limit: int, per: float) -> None:
    now = time.time()
    q = _hits[key]
    while q and now - q[0] > per:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many requests. Try again in a little while.")
    q.append(now)


def client_ip(request: Request) -> str:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")


def authed(request: Request) -> str:
    """The name making this request, proven by a signature over method, path and time."""
    name = (request.headers.get("x-xod-name") or "").lower()
    ts, sig = request.headers.get("x-xod-time") or "0", request.headers.get("x-xod-sig") or ""
    try:
        ts_i = int(ts)
    except ValueError:
        raise HTTPException(401, "Bad signature time.")
    if abs(time.time() - ts_i) > CLOCK_SKEW:
        raise HTTPException(401, "Your clock is off, or this request is old.")
    with db() as c:
        row = c.execute("SELECT sign_pub FROM names WHERE name=?", (name,)).fetchone()
    msg = f"xod-relay|{request.method.upper()}|{request.url.path}|{name}|{ts_i}".encode()
    if not row or not verify(row["sign_pub"], msg, sig):
        raise HTTPException(401, "Signature didn't match this name.")
    return name


def expire() -> None:
    with db() as c:
        c.execute("DELETE FROM mail WHERE received_at < ?", (time.time() - EXPIRE_DAYS * 86400,))


# ---------------------------------------------------------------- API

@app.get("/.well-known/xod-relay")
def about() -> Dict[str, Any]:
    return {"service": "xod-relay", "version": 1, "domain": DOMAIN, "max_envelope_bytes": MAX_ENVELOPE,
            "expire_days": EXPIRE_DAYS, "directory": True,
            "directory_heartbeat_seconds": DIRECTORY_HEARTBEAT, "directory_stale_after_seconds": DIRECTORY_STALE_AFTER}


class Registration(BaseModel):
    name: str
    sign_pub: str
    box_pub: str
    ts: int
    sig: str


@app.post("/v1/names")
def register(reg: Registration, request: Request) -> Dict[str, Any]:
    rate_limit(f"reg:{client_ip(request)}", 10, 3600)
    name = reg.name.lower()
    if not NAME_RE.match(name) or name in RESERVED:
        raise HTTPException(400, "Names are 2-32 letters, numbers, dots, dashes or underscores, and some are reserved.")
    if abs(time.time() - reg.ts) > CLOCK_SKEW:
        raise HTTPException(400, "Your clock is off.")
    if not verify(reg.sign_pub, f"xod-register|{name}|{reg.sign_pub}|{reg.box_pub}|{reg.ts}".encode(), reg.sig):
        raise HTTPException(400, "The registration isn't signed by that key.")
    if len(b64d(reg.sign_pub)) != 32 or len(b64d(reg.box_pub)) != 32:
        raise HTTPException(400, "Bad keys.")
    now = time.time()
    with _lock, db() as c:
        row = c.execute("SELECT sign_pub FROM names WHERE name=?", (name,)).fetchone()
        if row and row["sign_pub"] != reg.sign_pub:
            raise HTTPException(409, f"{name}@{DOMAIN} is taken.")
        if row:   # same owner: may update its encryption key
            c.execute("UPDATE names SET box_pub=?, updated_at=? WHERE name=?", (reg.box_pub, now, name))
        else:
            c.execute("INSERT INTO names VALUES (?,?,?,?,?)", (name, reg.sign_pub, reg.box_pub, now, now))
    return {"address": f"{name}@{DOMAIN}", "name": name, "domain": DOMAIN}


@app.get("/v1/names/{name}")
def lookup(name: str, request: Request) -> Dict[str, Any]:
    rate_limit(f"look:{client_ip(request)}", 240, 60)
    with db() as c:
        row = c.execute("SELECT * FROM names WHERE name=?", (name.lower(),)).fetchone()
    if not row:
        raise HTTPException(404, f"There's no {name}@{DOMAIN}.")
    return {"name": row["name"], "address": f"{row['name']}@{DOMAIN}", "sign_pub": row["sign_pub"],
            "box_pub": row["box_pub"], "registered_at": row["registered_at"]}


@app.delete("/v1/names/{name}")
def unregister(name: str, request: Request) -> Dict[str, Any]:
    who = authed(request)
    if who != name.lower():
        raise HTTPException(403, "You can only remove your own name.")
    with _lock, db() as c:
        c.execute("DELETE FROM mail WHERE name=?", (who,))
        c.execute("DELETE FROM names WHERE name=?", (who,))
    return {"removed": True}


class Post(BaseModel):
    to: str
    envelope: Dict[str, Any]


@app.post("/v1/mail", status_code=202)
def post_mail(post: Post, request: Request) -> Dict[str, Any]:
    ip = client_ip(request)
    rate_limit(f"post:{ip}", 60, 60)
    name = post.to.split("@")[0].lower()
    rate_limit(f"to:{name}", 120, 60)
    raw = json.dumps(post.envelope, separators=(",", ":"))
    if len(raw) > MAX_ENVELOPE:
        raise HTTPException(413, "That message is too big.")
    if not all(k in post.envelope for k in ("eph", "nonce", "ct")):
        raise HTTPException(400, "Not a sealed envelope.")
    with _lock, db() as c:
        if not c.execute("SELECT 1 FROM names WHERE name=?", (name,)).fetchone():
            raise HTTPException(404, f"There's no {name}@{DOMAIN}.")
        if c.execute("SELECT COUNT(*) FROM mail WHERE name=?", (name,)).fetchone()[0] >= MAILBOX_LIMIT:
            raise HTTPException(507, "That mailbox is full.")
        mid = uuid.uuid4().hex
        c.execute("INSERT INTO mail VALUES (?,?,?,?)", (mid, name, raw, time.time()))
    return {"id": mid}


@app.get("/v1/mail")
def fetch_mail(request: Request) -> Dict[str, List[Dict[str, Any]]]:
    who = authed(request)
    rate_limit(f"fetch:{who}", 120, 60)
    expire()
    with db() as c:
        rows = c.execute("SELECT id, envelope, received_at FROM mail WHERE name=? ORDER BY received_at LIMIT 100",
                         (who,)).fetchall()
    return {"messages": [{"id": r["id"], "envelope": json.loads(r["envelope"]), "received_at": r["received_at"]}
                         for r in rows]}


class Ack(BaseModel):
    ids: List[str]


@app.post("/v1/mail/ack")
def ack(body: Ack, request: Request) -> Dict[str, int]:
    who = authed(request)
    with _lock, db() as c:
        n = 0
        for mid in body.ids[:200]:
            n += c.execute("DELETE FROM mail WHERE id=? AND name=?", (mid, who)).rowcount
    return {"deleted": n}


# ---------------------------------------------------------------- self-host directory
# Public data only: address -> reachable endpoint URL + public keys. No message content ever
# lives here. Registration is challenge-verified: the relay only lists a record after the
# address's Ed25519 key signs a relay-issued challenge, so records can't be forged or replayed.

def _dir_address(address: str) -> str:
    address = (address or "").strip().lower()
    name, _, domain = address.partition("@")
    if not (name and domain and NAME_RE.match(name)):
        raise HTTPException(400, "The address must look like name@domain.")
    return address


def _dir_endpoint_ok(url: str) -> bool:
    m = re.match(r"^(https?)://([^/:]+)(?::(\d+))?(/.*)?$", url or "", re.I)
    if not m:
        return False
    if m.group(1).lower() == "https":
        return True
    host = m.group(2).lower()   # plain http only for local endpoints, like the client's relay_base
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local") or host.endswith(".localhost")


def _dir_keys_ok(sign_pub: str, box_pub: str) -> bool:
    try:
        return len(b64d(sign_pub)) == 32 and len(b64d(box_pub)) == 32
    except Exception:
        return False


class DirectoryRegistration(BaseModel):
    address: str
    endpoint: str
    sign_pub: str
    box_pub: str
    ts: int
    sig: str


@app.post("/v1/directory/register")
def directory_register(reg: DirectoryRegistration, request: Request) -> Dict[str, Any]:
    rate_limit(f"dirreg:{client_ip(request)}", 10, 3600)
    address = _dir_address(reg.address)
    if not _dir_endpoint_ok(reg.endpoint):
        raise HTTPException(400, "The endpoint must be an https:// URL (http:// is only for local hosts).")
    if abs(time.time() - reg.ts) > CLOCK_SKEW:
        raise HTTPException(400, "Your clock is off.")
    if not _dir_keys_ok(reg.sign_pub, reg.box_pub):
        raise HTTPException(400, "Bad keys.")
    msg = f"xod-dir-register|{address}|{reg.endpoint}|{reg.sign_pub}|{reg.box_pub}|{reg.ts}".encode()
    if not verify(reg.sign_pub, msg, reg.sig):
        raise HTTPException(400, "The registration isn't signed by that key.")
    now = time.time()
    with _lock, db() as c:
        row = c.execute("SELECT sign_pub, status FROM directory WHERE address=?", (address,)).fetchone()
        if row and row["sign_pub"] != reg.sign_pub:
            raise HTTPException(409, f"{address} is already registered with a different key.")
        if row and row["status"] == "live":
            c.execute("""UPDATE directory SET endpoint=?, box_pub=?, last_seen=? WHERE address=?""",
                      (reg.endpoint, reg.box_pub, now, address))
            return {"registered": True, "address": address, "endpoint": reg.endpoint}
        challenge_id, challenge = uuid.uuid4().hex, uuid.uuid4().hex
        c.execute("""INSERT INTO directory (address, endpoint, sign_pub, box_pub, status, challenge_id, challenge,
                                            registered_at, last_seen)
                     VALUES (?,?,?,?, 'pending',?,?,?,?)
                     ON CONFLICT(address) DO UPDATE SET endpoint=excluded.endpoint, sign_pub=excluded.sign_pub,
                       box_pub=excluded.box_pub, status='pending', challenge_id=excluded.challenge_id,
                       challenge=excluded.challenge, last_seen=excluded.last_seen""",
                  (address, reg.endpoint, reg.sign_pub, reg.box_pub, challenge_id, challenge, now, now))
    return {"status": "challenge", "challenge_id": challenge_id, "challenge": challenge}


class DirectoryChallenge(BaseModel):
    challenge_id: str
    address: str
    sig: str


@app.post("/v1/directory/challenge")
def directory_challenge(body: DirectoryChallenge, request: Request) -> Dict[str, Any]:
    rate_limit(f"dircha:{client_ip(request)}", 30, 3600)
    address = _dir_address(body.address)
    now = time.time()
    with _lock, db() as c:
        row = c.execute("SELECT * FROM directory WHERE challenge_id=? AND address=? AND status='pending'",
                        (body.challenge_id, address)).fetchone()
        if not row:
            raise HTTPException(400, "No such pending registration.")
        msg = f"xod-dir-challenge|{row['challenge_id']}|{row['challenge']}|{address}".encode()
        if not verify(row["sign_pub"], msg, body.sig):
            raise HTTPException(400, "The challenge answer isn't signed by that key.")
        c.execute("UPDATE directory SET status='live', challenge_id=NULL, challenge=NULL, last_seen=? WHERE address=?",
                  (now, address))
    return {"registered": True, "address": address, "endpoint": row["endpoint"]}


class DirectoryHeartbeat(BaseModel):
    address: str
    ts: int
    sig: str


@app.post("/v1/directory/heartbeat")
def directory_heartbeat(body: DirectoryHeartbeat, request: Request) -> Dict[str, Any]:
    address = _dir_address(body.address)
    rate_limit(f"dirbeat:{address}", 120, 3600)
    if abs(time.time() - body.ts) > CLOCK_SKEW:
        raise HTTPException(400, "Your clock is off.")
    now = time.time()
    with _lock, db() as c:
        row = c.execute("SELECT sign_pub, status FROM directory WHERE address=?", (address,)).fetchone()
        if not row or row["status"] != "live":
            raise HTTPException(404, f"{address} isn't registered.")
        if not verify(row["sign_pub"], f"xod-dir-heartbeat|{address}|{body.ts}".encode(), body.sig):
            raise HTTPException(400, "The heartbeat isn't signed by that key.")
        c.execute("UPDATE directory SET last_seen=? WHERE address=?", (now, address))
    return {"ok": True, "address": address, "last_seen": now}


@app.get("/v1/directory/{address}")
def directory_lookup(address: str, request: Request) -> Dict[str, Any]:
    rate_limit(f"dirlook:{client_ip(request)}", 240, 60)
    address = _dir_address(address)
    now = time.time()
    with _lock, db() as c:
        row = c.execute("SELECT * FROM directory WHERE address=?", (address,)).fetchone()
        if row and row["status"] == "live" and now - row["last_seen"] > DIRECTORY_EXPIRE_AFTER:
            c.execute("DELETE FROM directory WHERE address=?", (address,))
            row = None
    if not row or row["status"] != "live":
        raise HTTPException(404, f"{address} isn't registered.")
    return {"address": row["address"], "endpoint": row["endpoint"], "sign_pub": row["sign_pub"],
            "box_pub": row["box_pub"], "registered_at": row["registered_at"], "last_seen": row["last_seen"],
            "stale": (now - row["last_seen"]) > DIRECTORY_STALE_AFTER}
