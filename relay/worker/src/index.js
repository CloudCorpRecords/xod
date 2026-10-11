// Xod Post Office as a Cloudflare Worker: the same protocol as relay/server.py (see relay/README.md).
// It keeps a name directory and holds sealed, end-to-end encrypted envelopes until they're picked up,
// then deletes them. It can't read mail or see who sent it.

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

function b64d(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  s += "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(s);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

async function verify(signPub, message, sig) {
  try {
    const key = await crypto.subtle.importKey("raw", b64d(signPub), { name: "Ed25519" }, false, ["verify"]);
    return await crypto.subtle.verify({ name: "Ed25519" }, key, b64d(sig), new TextEncoder().encode(message));
  } catch (e) {
    return false;
  }
}

// Best effort, per isolate: enough to slow down a noisy client. Use Cloudflare rate limiting rules for more.
const hits = new Map();
function limited(key, limit, perSec) {
  const now = Date.now() / 1000;
  const q = (hits.get(key) || []).filter((t) => now - t < perSec);
  if (q.length >= limit) return true;
  q.push(now);
  hits.set(key, q);
  if (hits.size > 50000) hits.clear();
  return false;
}

async function authed(req, env, path) {
  const name = (req.headers.get("x-xod-name") || "").toLowerCase();
  const ts = parseInt(req.headers.get("x-xod-time") || "0", 10);
  const sig = req.headers.get("x-xod-sig") || "";
  if (!ts || Math.abs(Date.now() / 1000 - ts) > CLOCK_SKEW) return [null, fail(401, "Your clock is off, or this request is old.")];
  const row = await env.DB.prepare("SELECT sign_pub FROM names WHERE name=?").bind(name).first();
  if (!row || !(await verify(row.sign_pub, `xod-relay|${req.method.toUpperCase()}|${path}|${name}|${ts}`, sig))) {
    return [null, fail(401, "Signature didn't match this name.")];
  }
  return [name, null];
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const path = url.pathname;
    const ip = req.headers.get("cf-connecting-ip") || "?";
    const DOMAIN = env.DOMAIN || url.host;
    try {
      if (req.method === "GET" && path === "/.well-known/xod-relay") {
        return json({ service: "xod-relay", version: 1, domain: DOMAIN, max_envelope_bytes: MAX_ENVELOPE, expire_days: EXPIRE_DAYS });
      }

      if (req.method === "POST" && path === "/v1/names") {
        if (limited(`reg:${ip}`, 10, 3600)) return fail(429, "Too many requests. Try again in a little while.");
        const r = await req.json();
        const name = String(r.name || "").toLowerCase();
        if (!NAME_RE.test(name) || RESERVED.has(name)) return fail(400, "Names are 2-32 letters, numbers, dots, dashes or underscores, and some are reserved.");
        if (Math.abs(Date.now() / 1000 - Number(r.ts)) > CLOCK_SKEW) return fail(400, "Your clock is off.");
        if (!(await verify(r.sign_pub, `xod-register|${name}|${r.sign_pub}|${r.box_pub}|${r.ts}`, r.sig))) {
          return fail(400, "The registration isn't signed by that key.");
        }
        if (b64d(r.sign_pub).length !== 32 || b64d(r.box_pub).length !== 32) return fail(400, "Bad keys.");
        const now = Date.now() / 1000;
        const row = await env.DB.prepare("SELECT sign_pub FROM names WHERE name=?").bind(name).first();
        if (row && row.sign_pub !== r.sign_pub) return fail(409, `${name}@${DOMAIN} is taken.`);
        if (row) await env.DB.prepare("UPDATE names SET box_pub=?, updated_at=? WHERE name=?").bind(r.box_pub, now, name).run();
        else await env.DB.prepare("INSERT INTO names VALUES (?,?,?,?,?)").bind(name, r.sign_pub, r.box_pub, now, now).run();
        return json({ address: `${name}@${DOMAIN}`, name, domain: DOMAIN });
      }

      let m = path.match(/^\/v1\/names\/([^/]+)$/);
      if (m && req.method === "GET") {
        if (limited(`look:${ip}`, 240, 60)) return fail(429, "Too many requests.");
        const name = decodeURIComponent(m[1]).toLowerCase();
        const row = await env.DB.prepare("SELECT * FROM names WHERE name=?").bind(name).first();
        if (!row) return fail(404, `There's no ${name}@${DOMAIN}.`);
        return json({ name: row.name, address: `${row.name}@${DOMAIN}`, sign_pub: row.sign_pub, box_pub: row.box_pub,
                      registered_at: row.registered_at });
      }
      if (m && req.method === "DELETE") {
        const [who, err] = await authed(req, env, path);
        if (err) return err;
        if (who !== decodeURIComponent(m[1]).toLowerCase()) return fail(403, "You can only remove your own name.");
        await env.DB.batch([env.DB.prepare("DELETE FROM mail WHERE name=?").bind(who), env.DB.prepare("DELETE FROM names WHERE name=?").bind(who)]);
        return json({ removed: true });
      }

      if (path === "/v1/mail" && req.method === "POST") {
        if (limited(`post:${ip}`, 60, 60)) return fail(429, "Too many requests. Try again in a little while.");
        const post = await req.json();
        const name = String(post.to || "").split("@")[0].toLowerCase();
        if (limited(`to:${name}`, 120, 60)) return fail(429, "That mailbox is busy. Try again in a minute.");
        const e = post.envelope || {};
        const raw = JSON.stringify(e);
        if (raw.length > MAX_ENVELOPE) return fail(413, "That message is too big.");
        if (!e.eph || !e.nonce || !e.ct) return fail(400, "Not a sealed envelope.");
        if (!(await env.DB.prepare("SELECT 1 AS ok FROM names WHERE name=?").bind(name).first())) return fail(404, `There's no ${name}@${DOMAIN}.`);
        const count = await env.DB.prepare("SELECT COUNT(*) AS n FROM mail WHERE name=?").bind(name).first();
        if (count.n >= MAILBOX_LIMIT) return fail(507, "That mailbox is full.");
        const id = crypto.randomUUID().replace(/-/g, "");
        await env.DB.prepare("INSERT INTO mail VALUES (?,?,?,?)").bind(id, name, raw, Date.now() / 1000).run();
        return json({ id }, 202);
      }
      if (path === "/v1/mail" && req.method === "GET") {
        const [who, err] = await authed(req, env, path);
        if (err) return err;
        if (limited(`fetch:${who}`, 120, 60)) return fail(429, "Too many requests.");
        await env.DB.prepare("DELETE FROM mail WHERE received_at < ?").bind(Date.now() / 1000 - EXPIRE_DAYS * 86400).run();
        const { results } = await env.DB.prepare("SELECT id, envelope, received_at FROM mail WHERE name=? ORDER BY received_at LIMIT 100").bind(who).all();
        return json({ messages: results.map((r) => ({ id: r.id, envelope: JSON.parse(r.envelope), received_at: r.received_at })) });
      }
      if (path === "/v1/mail/ack" && req.method === "POST") {
        const [who, err] = await authed(req, env, path);
        if (err) return err;
        const body = await req.json();
        const ids = (body.ids || []).slice(0, 200);
        if (ids.length) await env.DB.batch(ids.map((id) => env.DB.prepare("DELETE FROM mail WHERE id=? AND name=?").bind(String(id), who)));
        return json({ deleted: ids.length });
      }
      return fail(404, "Not found.");
    } catch (e) {
      return fail(400, "Bad request.");
    }
  },
};
