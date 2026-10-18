// Xod Post Office on Deno Deploy, with its data in Deno KV (Deno Deploy's built-in database): the same relay as
// ../relay.mjs runs on Vercel and in memory. Deploy and test: see relay/README.md.
//   deno run --unstable-kv --allow-net --allow-env relay/deno/main.mjs     # on this computer (KV in memory)
import { handle } from "../relay.mjs";

// Deno KV keeps each value under 64 KB, and an envelope may be 256 KB: big values are split into pieces.
const PIECE = 60 * 1024;

/** The Redis commands relay.mjs uses, on Deno KV. Strings live at ["s", key], hash fields at ["h", key, field]. */
export function kvStore(kv) {
  const now = () => Date.now();

  async function putHash(key, field, value) {
    const pieces = Math.ceil(value.length / PIECE) || 1;
    const tx = kv.atomic().set(["h", key, field], { pieces });
    for (let i = 0; i < pieces; i++) tx.set(["p", key, field, i], value.slice(i * PIECE, (i + 1) * PIECE));
    await tx.commit();
  }
  async function getHash(key, field, head) {
    const parts = [];
    for (let i = 0; i < head.pieces; i++) parts.push((await kv.get(["p", key, field, i])).value || "");
    return parts.join("");
  }
  async function delHash(key, field) {
    const head = (await kv.get(["h", key, field])).value;
    const tx = kv.atomic().delete(["h", key, field]);
    for (let i = 0; i < (head?.pieces || 0); i++) tx.delete(["p", key, field, i]);
    await tx.commit();
    return head ? 1 : 0;
  }
  async function fields(key) {
    const out = [];
    for await (const e of kv.list({ prefix: ["h", key] })) out.push([e.key[2], e.value]);
    return out;
  }

  async function run([op, key, ...a]) {
    switch (op.toUpperCase()) {
      case "GET": {
        const v = (await kv.get(["s", key])).value;
        return v && (!v.until || v.until > now()) ? v.v : null;
      }
      case "SET": await kv.set(["s", key], { v: a[0] }); return "OK";
      case "DEL": {
        let n = 0;
        for (const k of [key, ...a]) {
          if ((await kv.get(["s", k])).value) { await kv.delete(["s", k]); n++; }
          for (const [f] of await fields(k)) n += await delHash(k, f);
        }
        return n;
      }
      case "INCR": {     // a rate-limit counter: its window starts at the first hit and lasts until EXPIRE's time
        const cur = (await kv.get(["s", key])).value;
        const live = cur && (!cur.until || cur.until > now());
        const v = { v: String((live ? Number(cur.v) : 0) + 1), until: live ? cur.until : null };
        await kv.set(["s", key], v, v.until ? { expireIn: Math.max(1000, v.until - now()) } : {});
        return Number(v.v);
      }
      case "EXPIRE": {
        const cur = (await kv.get(["s", key])).value;
        if (!cur) return 0;
        if (!cur.until) {
          cur.until = now() + Number(a[0]) * 1000;
          await kv.set(["s", key], cur, { expireIn: Number(a[0]) * 1000 });
        }
        return 1;
      }
      case "HSET": for (let i = 0; i < a.length; i += 2) await putHash(key, a[i], a[i + 1]); return 1;
      case "HLEN": return (await fields(key)).length;
      case "HGETALL": {
        const out = [];
        for (const [f, head] of await fields(key)) out.push(f, await getHash(key, f, head));
        return out;
      }
      case "HDEL": { let n = 0; for (const f of a) n += await delHash(key, f); return n; }
      default: throw new Error(`unknown command ${op}`);
    }
  }
  return { cmd: (...c) => run(c), pipe: async (cmds) => { const out = []; for (const c of cmds) out.push(await run(c)); return out; } };
}

const kv = await Deno.openKv(Deno.env.get("XOD_RELAY_KV") || undefined);
const store = kvStore(kv);
const port = Number(Deno.env.get("PORT") || 8794);

Deno.serve({ port }, (request, info) => {
  const url = new URL(request.url);
  const domain = Deno.env.get("XOD_RELAY_DOMAIN") || url.host;
  return handle(request, store, domain, url.pathname, info?.remoteAddr?.hostname || "?");
});
