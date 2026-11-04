"""
The resource registry (orchestrator scope, section 12; Phase 3).

Named resources with a capacity (the Mac's screen, each phone, the agent browser, each signed-in
account session). A holder (a task, a worker, a chat: its activity origin) claims a resource
before using it. Claims are single-writer and FIFO: a claim is granted while the resource has a
free slot and nobody queued earlier, otherwise it queues with a visible reason ("held by X since
T"). A held claim carries a heartbeat; a holder that dies stops beating, its claim expires, and
the next queued claim is granted. A busy resource is defer-and-recheck, never a fight.

Claims are sticky per holder: a task holding the browser keeps it across its tool calls (each
call beats the heartbeat) until it finishes or goes idle past the TTL, so two lanes never
interleave on one window or one phone.
"""
import asyncio
import contextlib
import logging
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xod.resources")

DEFAULT_TTL_SEC = 120          # a claim not beaten for this long has expired (its holder is gone or idle)
DEFAULT_CAPACITY = {"browser": 1, "mac_ui": 1}

# Which resource a tool call needs. Phones are per address: phone:<address or default>.
BROWSER_TOOLS = {"browser_click", "browser_dismiss_overlays", "browser_evaluate_script", "browser_export_pdf",
                 "browser_extract_ax_tree", "browser_extract_content", "browser_extract_data", "browser_extract_markdown",
                 "browser_fill_form", "browser_get_downloads", "browser_handle_captcha", "browser_hover", "browser_interact",
                 "browser_manage_cookies", "browser_manage_tabs", "browser_navigate", "browser_observe", "browser_press_key",
                 "browser_restart", "browser_run_mission", "browser_scroll", "browser_set_visibility", "browser_smart_inspect",
                 "browser_snapshot", "browser_snoop_network", "browser_type", "browser_unstuck", "browser_upload_file",
                 "browser_wait_for"}


def resource_for(tool: str, args: Dict[str, Any]) -> Optional[str]:
    if tool in BROWSER_TOOLS:
        return "browser"
    if tool.startswith("desktop_") and tool != "desktop_setup":
        return "mac_ui"
    if tool == "phone":
        return f"phone:{(args or {}).get('phone') or 'default'}"
    return None


def _conn() -> sqlite3.Connection:
    from app.services import task_queue as TQ
    c = TQ._conn()
    c.execute("""CREATE TABLE IF NOT EXISTS resources (
        name TEXT PRIMARY KEY, capacity INTEGER NOT NULL, note TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS resource_claims (
        id TEXT PRIMARY KEY,
        resource TEXT NOT NULL,
        holder_id TEXT NOT NULL,
        state TEXT NOT NULL,            -- queued | granted | released | expired | abandoned
        reason TEXT,
        requested_at REAL NOT NULL,
        granted_at REAL,
        heartbeat_at REAL,
        expires_at REAL,
        ended_at REAL,
        ttl REAL)""")
    c.execute("CREATE INDEX IF NOT EXISTS resource_claims_live ON resource_claims(resource, state)")
    return c


def register(name: str, capacity: int = 1, note: str = "") -> None:
    c = _conn()
    c.execute("INSERT INTO resources (name, capacity, note) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE SET capacity = ?, note = ?",
              (name, capacity, note, capacity, note))
    c.commit()
    c.close()


def _capacity(c: sqlite3.Connection, name: str) -> int:
    r = c.execute("SELECT capacity FROM resources WHERE name = ?", (name,)).fetchone()
    if r:
        return int(r["capacity"])
    cap = DEFAULT_CAPACITY.get(name, 1)
    c.execute("INSERT OR IGNORE INTO resources (name, capacity) VALUES (?,?)", (name, cap))
    return cap


def _emit(event: str, **payload: Any) -> None:
    try:
        from app.services import event_log
        event_log.emit(event, actor="resources", payload=payload)
    except Exception:
        pass


def _sweep(c: sqlite3.Connection, name: str, now: float) -> None:
    """Expires claims whose heartbeat stopped, then grants queued claims in arrival order."""
    for r in c.execute("SELECT id, holder_id FROM resource_claims WHERE resource = ? AND state = 'granted' AND expires_at < ?",
                       (name, now)).fetchall():
        c.execute("UPDATE resource_claims SET state = 'expired', ended_at = ? WHERE id = ?", (now, r["id"]))
        _emit("resource.expired", resource=name, holder=r["holder_id"], claim=r["id"])
    cap = _capacity(c, name)
    held = c.execute("SELECT COUNT(*) AS n FROM resource_claims WHERE resource = ? AND state = 'granted'", (name,)).fetchone()["n"]
    for r in c.execute("SELECT id, holder_id, ttl FROM resource_claims WHERE resource = ? AND state = 'queued' "
                       "ORDER BY requested_at ASC", (name,)).fetchall():
        if held >= cap:
            break
        ttl = r["ttl"] or DEFAULT_TTL_SEC
        c.execute("UPDATE resource_claims SET state = 'granted', granted_at = ?, heartbeat_at = ?, expires_at = ?, reason = NULL "
                  "WHERE id = ?", (now, now, now + ttl, r["id"]))
        held += 1
        _emit("resource.granted", resource=name, holder=r["holder_id"], claim=r["id"])
    # Refresh the visible reason on whoever still waits.
    holders = c.execute("SELECT holder_id, granted_at FROM resource_claims WHERE resource = ? AND state = 'granted' "
                        "ORDER BY granted_at", (name,)).fetchall()
    if holders:
        why = "; ".join(f"held by {h['holder_id']} since {time.strftime('%H:%M:%S', time.localtime(h['granted_at']))}" for h in holders)
        c.execute("UPDATE resource_claims SET reason = ? WHERE resource = ? AND state = 'queued'", (f"{name} is busy: {why}", name))


def acquire(name: str, holder: str, ttl: float = DEFAULT_TTL_SEC) -> Dict[str, Any]:
    """Claims a slot, or queues for one. Re-claiming a resource the holder already holds beats
    the heartbeat and returns the same grant (claims are sticky per holder)."""
    now = time.time()
    c = _conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        _sweep(c, name, now)
        mine = c.execute("SELECT * FROM resource_claims WHERE resource = ? AND holder_id = ? AND state IN ('granted','queued') "
                         "ORDER BY requested_at LIMIT 1", (name, holder)).fetchone()
        if mine is None:
            cid = f"claim-{uuid.uuid4().hex[:8]}"
            c.execute("INSERT INTO resource_claims (id, resource, holder_id, state, requested_at, ttl) VALUES (?,?,?,?,?,?)",
                      (cid, name, holder, "queued", now, ttl))
            _emit("resource.requested", resource=name, holder=holder, claim=cid)
            _sweep(c, name, now)
            mine = c.execute("SELECT * FROM resource_claims WHERE id = ?", (cid,)).fetchone()
        elif mine["state"] == "granted":
            c.execute("UPDATE resource_claims SET heartbeat_at = ?, expires_at = ? WHERE id = ?", (now, now + (mine["ttl"] or ttl), mine["id"]))
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        c.close()
        raise
    out = {"claim_id": mine["id"], "resource": name, "holder": holder, "granted": mine["state"] == "granted",
           "state": mine["state"], "reason": mine["reason"]}
    c.close()
    return out


def heartbeat(claim_id: str) -> bool:
    now = time.time()
    c = _conn()
    n = c.execute("UPDATE resource_claims SET heartbeat_at = ?, expires_at = ? + COALESCE(ttl, ?) WHERE id = ? AND state = 'granted'",
                  (now, now, DEFAULT_TTL_SEC, claim_id)).rowcount
    c.commit()
    c.close()
    return bool(n)


def release(claim_id: str, state: str = "released") -> None:
    now = time.time()
    c = _conn()
    r = c.execute("SELECT resource, holder_id FROM resource_claims WHERE id = ?", (claim_id,)).fetchone()
    c.execute("UPDATE resource_claims SET state = ?, ended_at = ? WHERE id = ? AND state IN ('granted','queued')", (state, now, claim_id))
    if r:
        _emit("resource.released", resource=r["resource"], holder=r["holder_id"], claim=claim_id, state=state)
        _sweep(c, r["resource"], now)
    c.commit()
    c.close()
    _notify()


def release_holder(holder: str) -> int:
    """Everything a finished holder (a task, a worker) still holds or waits for is let go."""
    c = _conn()
    ids = [r["id"] for r in c.execute("SELECT id FROM resource_claims WHERE holder_id = ? AND state IN ('granted','queued')",
                                      (holder,)).fetchall()]
    c.close()
    for i in ids:
        release(i)
    return len(ids)


def status(name: Optional[str] = None) -> List[Dict[str, Any]]:
    now = time.time()
    c = _conn()
    names = [name] if name else [r["name"] for r in c.execute("SELECT name FROM resources").fetchall()]
    out = []
    for n in names:
        _sweep(c, n, now)
        rows = c.execute("SELECT * FROM resource_claims WHERE resource = ? AND state IN ('granted','queued') ORDER BY requested_at",
                         (n,)).fetchall()
        out.append({"resource": n, "capacity": _capacity(c, n),
                    "holders": [dict(r) for r in rows if r["state"] == "granted"],
                    "queue": [dict(r) for r in rows if r["state"] == "queued"]})
    c.commit()
    c.close()
    return out


# --------------------------------------------------------------------- waiting for a grant
_changed: Optional[asyncio.Event] = None


def _notify() -> None:
    global _changed
    try:
        if _changed is not None:
            _changed.set()
    except Exception:
        pass


async def wait_for(name: str, holder: str, max_wait: float, ttl: float = DEFAULT_TTL_SEC,
                   recheck_sec: float = 1.0) -> Dict[str, Any]:
    """Claims the resource, waiting (queued, with its reason recorded) up to max_wait. Wakes on
    any release, and rechecks periodically so an expired holder's slot passes over. On timeout
    the queued claim is abandoned and the busy reason returned: defer, never fight."""
    global _changed
    if _changed is None:
        _changed = asyncio.Event()
    deadline = time.time() + max_wait
    while True:
        got = acquire(name, holder, ttl)
        if got["granted"]:
            return got
        if time.time() >= deadline:
            release(got["claim_id"], state="abandoned")
            return {**got, "granted": False, "state": "deferred"}
        _changed.clear()
        try:
            await asyncio.wait_for(_changed.wait(), timeout=min(recheck_sec, max(0.05, deadline - time.time())))
        except asyncio.TimeoutError:
            pass


@contextlib.asynccontextmanager
async def held(name: str, holder: str, max_wait: float = 600, ttl: float = DEFAULT_TTL_SEC):
    """Holds a resource for a block, beating the heartbeat while the block runs."""
    got = await wait_for(name, holder, max_wait, ttl)
    if not got["granted"]:
        yield got
        return

    async def beat():
        while True:
            await asyncio.sleep(max(1.0, ttl / 3))
            heartbeat(got["claim_id"])
    hb = asyncio.ensure_future(beat())
    try:
        yield got
    finally:
        hb.cancel()
        release(got["claim_id"])
