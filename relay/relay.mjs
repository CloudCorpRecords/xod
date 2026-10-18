// Xod Post Office on Vercel: the same protocol as relay/server.py and relay/worker (see relay/README.md), with its data
// in Redis (Upstash, Vercel's one-click Redis). It keeps a name directory and holds sealed, end-to-end encrypted
// envelopes until they're picked up, then deletes them. It can't read mail or see who sent it.
//
// handle(request, store, domain, path) is the whole relay: vercel/ runs it on Vercel with Redis, deno/ on Deno Deploy with
// Deno KV, vercel/dev.mjs on this computer in memory.
//
// Redis keys:  name:<name>   JSON {sign_pub, box_pub, registered_at, updated_at}
//              box:<name>    hash  id -> JSON {envelope, received_at}
//              rl:<key>      a counter for rate limits (expires)

const MAX_ENVELOPE = 256 * 1024;
const MAILBOX_LIMIT = 500;
const EXPIRE_DAYS = 30;
const CLOCK_SKEW = 300;
const NAME_RE = /^[a-z0-9](?:[a-z0-9._-]{0,30}[a-z0-9])?$/;
const RESERVED = new Set(["admin", "administrator", "root", "postmaster", "abuse", "support", "help", "security", "xod",
  "relay", "system", "noreply", "no-reply", "mail", "www", "api", "official", "team", "staff"]);

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
const fail = (status, detail) => json({ detail }, status);
const now = () => Date.now() / 1000;

function b64d(s) {
  s = String(s || "").replace(/-/g, "+").replace(/_/g, "/");
  s += "=".repeat((4 - (s.length % 4)) % 4);
  return Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
}

async function verify(signPub, message, sig) {
  try {
    const key = await crypto.subtle.importKey("raw", b64d(signPub), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify({ name: "Ed25519" }, key, b64d(sig), new TextEncoder().encode(message));
  } catch (e) {
    return false;
  }
}

// ------------------------------------------------------------------ storage

/** Redis over Upstash's REST API (what Vercel's Redis gives you: KV_REST_API_URL / UPSTASH_REDIS_REST_URL and a token). */
export function upstash(url, token) {
  const call = async (path, body) => {
    const r = await fetch(url.replace(/\/$/, "") + path, {
      method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error(`Redis answered ${r.status}`);
    return r.json();
  };
  return {
    cmd: async (...args) => (await call("", args)).result,
    pipe: async (cmds) => (await call("/pipeline", cmds)).map((x) => x.result),
  };
}

/** The same commands, in memory: for running the relay on this computer and in tests. */
export function memory() {
  const kv = new Map(), hashes = new Map(), exp = new Map();
  const alive = (k) => { if (exp.has(k) && exp.get(k) < Date.now()) { kv.delete(k); exp.delete(k); } };
  const run = (c) => {
    const [op, k, ...a] = c; alive(k);
    switch (op.toUpperCase()) {
      case "GET": return kv.has(k) ? kv.get(k) : null;
      case "SET": kv.set(k, a[0]); return "OK";
      case "DEL": { let n = 0; for (const key of [k, ...a]) n += Number(kv.delete(key)) + Number(hashes.delete(key)); return n; }
      case "INCR": { const v = Number(kv.get(k) || 0) + 1; kv.set(k, String(v)); return v; }
      case "EXPIRE": exp.set(k, Date.now() + Number(a[0]) * 1000); return 1;
      case "HSET": { const h = hashes.get(k) || new Map(); hashes.set(k, h); for (let i = 0; i < a.length; i += 2) h.set(a[i], a[i + 1]); return 1; }
      case "HLEN": return (hashes.get(k) || new Map()).size;
      case "HGETALL": return [...(hashes.get(k) || new Map())].flat();
      case "HDEL": { const h = hashes.get(k) || new Map(); let n = 0; for (const f of a) n += Number(h.delete(f)); return n; }
      default: throw new Error(`unknown command ${op}`);
    }
  };
  return { cmd: async (...c) => run(c), pipe: async (cmds) => cmds.map(run) };
}

/** Whichever the environment has: Vercel's Redis, or memory (says so, since memory is lost on restart). */
export function storeFromEnv(env) {
  const url = env.KV_REST_API_URL || env.UPSTASH_REDIS_REST_URL;
  const token = env.KV_REST_API_TOKEN || env.UPSTASH_REDIS_REST_TOKEN;
  return url && token ? upstash(url, token) : null;
}

// ------------------------------------------------------------------ the relay

async function limited(store, key, limit, perSec) {
  const [n] = await store.pipe([["INCR", `rl:${key}`], ["EXPIRE", `rl:${key}`, String(perSec)]]);
  return Number(n) > limit;
}

async function getName(store, name) {
  const raw = await store.cmd("GET", `name:${name}`);
  return raw ? JSON.parse(raw) : null;
}

async function authed(req, store, path) {
  const name = (req.headers.get("x-xod-name") || "").toLowerCase();
  const ts = parseInt(req.headers.get("x-xod-time") || "0", 10);
  const sig = req.headers.get("x-xod-sig") || "";
  if (!ts || Math.abs(now() - ts) > CLOCK_SKEW) return [null, fail(401, "Your clock is off, or this request is old.")];
  const row = NAME_RE.test(name) ? await getName(store, name) : null;
  if (!row || !(await verify(row.sign_pub, `xod-relay|${req.method.toUpperCase()}|${path}|${name}|${ts}`, sig))) {
    return [null, fail(401, "Signature didn't match this name.")];
  }
  return [name, null];
}

function pairs(flat) {
  const out = [];
  for (let i = 0; i + 1 < (flat || []).length; i += 2) out.push([flat[i], flat[i + 1]]);
  return out;
}

export async function handle(req, store, domain, path, ip = "?") {
  const DOMAIN = domain || new URL(req.url).host;
  try {
    if (req.method === "GET" && (path === "/.well-known/xod-relay" || path === "/")) {
      return json({ service: "xod-relay", version: 1, domain: DOMAIN, max_envelope_bytes: MAX_ENVELOPE, expire_days: EXPIRE_DAYS });
    }

    if (req.method === "POST" && path === "/v1/names") {
      if (await limited(store, `reg:${ip}`, 10, 3600)) return fail(429, "Too many requests. Try again in a little while.");
      const r = await req.json();
      const name = String(r.name || "").toLowerCase();
      if (!NAME_RE.test(name) || RESERVED.has(name)) return fail(400, "Names are 2-32 letters, numbers, dots, dashes or underscores, and some are reserved.");
      if (Math.abs(now() - Number(r.ts)) > CLOCK_SKEW) return fail(400, "Your clock is off.");
      if (!(await verify(r.sign_pub, `xod-register|${name}|${r.sign_pub}|${r.box_pub}|${r.ts}`, r.sig))) {
        return fail(400, "The registration isn't signed by that key.");
      }
      if (b64d(r.sign_pub).length !== 32 || b64d(r.box_pub).length !== 32) return fail(400, "Bad keys.");
      const row = await getName(store, name);
      if (row && row.sign_pub !== r.sign_pub) return fail(409, `${name}@${DOMAIN} is taken.`);
      const t = now();
      await store.cmd("SET", `name:${name}`, JSON.stringify({ sign_pub: r.sign_pub, box_pub: r.box_pub,
        registered_at: row ? row.registered_at : t, updated_at: t }));
      return json({ address: `${name}@${DOMAIN}`, name, domain: DOMAIN });
    }

    const m = path.match(/^\/v1\/names\/([^/]+)$/);
    if (m && req.method === "GET") {
      if (await limited(store, `look:${ip}`, 240, 60)) return fail(429, "Too many requests.");
      const name = decodeURIComponent(m[1]).toLowerCase();
      const row = NAME_RE.test(name) ? await getName(store, name) : null;
      if (!row) return fail(404, `There's no ${name}@${DOMAIN}.`);
      return json({ name, address: `${name}@${DOMAIN}`, sign_pub: row.sign_pub, box_pub: row.box_pub, registered_at: row.registered_at });
    }
    if (m && req.method === "DELETE") {
      const [who, err] = await authed(req, store, path);
      if (err) return err;
      if (who !== decodeURIComponent(m[1]).toLowerCase()) return fail(403, "You can only remove your own name.");
      await store.cmd("DEL", `name:${who}`, `box:${who}`);
      return json({ removed: true });
    }

    if (path === "/v1/mail" && req.method === "POST") {
      if (await limited(store, `post:${ip}`, 60, 60)) return fail(429, "Too many requests. Try again in a little while.");
      const post = await req.json();
      const name = String(post.to || "").split("@")[0].toLowerCase();
      if (await limited(store, `to:${name}`, 120, 60)) return fail(429, "That mailbox is busy. Try again in a minute.");
      const e = post.envelope || {};
      const raw = JSON.stringify(e);
      if (raw.length > MAX_ENVELOPE) return fail(413, "That message is too big.");
      if (!e.eph || !e.nonce || !e.ct) return fail(400, "Not a sealed envelope.");
      if (!NAME_RE.test(name) || !(await getName(store, name))) return fail(404, `There's no ${name}@${DOMAIN}.`);
      if (Number(await store.cmd("HLEN", `box:${name}`)) >= MAILBOX_LIMIT) return fail(507, "That mailbox is full.");
      const id = crypto.randomUUID().replace(/-/g, "");
      await store.cmd("HSET", `box:${name}`, id, JSON.stringify({ envelope: e, received_at: now() }));
      return json({ id }, 202);
    }
    if (path === "/v1/mail" && req.method === "GET") {
      const [who, err] = await authed(req, store, path);
      if (err) return err;
      if (await limited(store, `fetch:${who}`, 120, 60)) return fail(429, "Too many requests.");
      const cutoff = now() - EXPIRE_DAYS * 86400;
      const all = pairs(await store.cmd("HGETALL", `box:${who}`)).map(([id, v]) => ({ id, ...JSON.parse(v) }));
      const old = all.filter((x) => x.received_at < cutoff).map((x) => x.id);
      if (old.length) await store.cmd("HDEL", `box:${who}`, ...old);
      const messages = all.filter((x) => x.received_at >= cutoff).sort((a, b) => a.received_at - b.received_at).slice(0, 100);
      return json({ messages });
    }
    if (path === "/v1/mail/ack" && req.method === "POST") {
      const [who, err] = await authed(req, store, path);
      if (err) return err;
      const ids = ((await req.json()).ids || []).slice(0, 200).map(String);
      if (ids.length) await store.cmd("HDEL", `box:${who}`, ...ids);
      return json({ deleted: ids.length });
    }
    return fail(404, "Not found.");
  } catch (e) {
    return fail(400, "Bad request.");
  }
}
