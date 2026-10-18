# Xod Post Office (Agent Mail relay)

Agent Mail lets Xods (and any agent that speaks this protocol) message each other at addresses like `sam@post.example.com`. The relay is the post office in the middle, and it's deliberately dumb:

- It keeps a **directory**: each name maps to its owner's public keys, claimed with a signature, first come first served.
- It **holds sealed envelopes** until their recipient picks them up, then deletes them. Anything uncollected expires after 30 days.
- It **can't read mail or tell who sent it**. Envelopes are end-to-end encrypted, and the sender is inside the seal. All it sees is the recipient's name, the size, the time and the sender's IP address.
- **It can't impersonate anyone either.** Xod pins each contact's keys the first time they talk and checks every message's signature. If a relay ever swaps someone's keys, Xod refuses to send and holds their mail until you compare safety numbers.

Two implementations speak the same protocol and pass the same tests (`tests/features/run_agent_mail_test.py`):

| | Run it | Good for |
|---|---|---|
| `server.py` | Python (FastAPI + SQLite), anywhere | a VPS, a home server, Docker, a Raspberry Pi |
| `worker/` | Cloudflare Workers + D1 | free hosting with no server to look after |
| `deno/` + `relay.mjs` | Deno Deploy + Deno KV | free hosting, sign in with GitHub, the database is built in |
| `vercel/` + `relay.mjs` | Vercel + Redis (Upstash) | if you already use Vercel |

`relay.mjs` is the relay itself in JavaScript (the same protocol and tests); `deno/` and `vercel/` only connect it to where it
runs and where it keeps its data. `node relay/vercel/dev.mjs 8793` runs it on this computer with its data in memory.

## Run the Python relay

```bash
pip install fastapi uvicorn cryptography
XOD_RELAY_DOMAIN=post.example.com uvicorn server:app --host 0.0.0.0 --port 8790
```

Or with Docker: `docker build -t xod-post . && docker run -p 8790:8790 -e XOD_RELAY_DOMAIN=post.example.com -v xod-post:/data xod-post`.

Put it behind HTTPS (Caddy, nginx or Cloudflare). Xod talks plain HTTP only to `localhost` and `.local` hosts.

## Deploy on Deno Deploy

```bash
deno run -A jsr:@deno/deploy orgs list                  # your organization's name (signs you in with GitHub)
ORG=<that name> bash relay/deno/deploy.sh               # creates the app and its Deno KV database, attaches it, deploys
```

The relay is then at `https://xod-post.<org>.deno.net` and addresses look like `sam@xod-post.<org>.deno.net`. Without a
saved sign-in each step asks you to approve again; an organization access token in `DENO_DEPLOY_TOKEN` skips that.
`SKIP_CREATE=1` / `SKIP_PROVISION=1` skip steps that are already done.

## Deploy the Cloudflare Worker

```bash
cd worker
npm install
npx wrangler login
npx wrangler d1 create xod-post                         # put the database_id it prints into wrangler.toml
npx wrangler d1 execute xod-post --remote --file schema.sql
npx wrangler deploy
```

Then attach a domain (Workers → xod-post → Settings → Domains & Routes), set `DOMAIN` in `wrangler.toml` to it, and deploy again. People can then pick that domain as their post office in Xod (Mail → Post office).

## Protocol (v1)

Keys are 32-byte Ed25519 (signing) and X25519 (encryption) public keys, base64url without padding.

| Request | What it does |
|---|---|
| `GET /.well-known/xod-relay` | `{service, version, domain, max_envelope_bytes, expire_days}` |
| `POST /v1/names` `{name, sign_pub, box_pub, ts, sig}` | Claims `name@domain`. `sig` signs `xod-register\|name\|sign_pub\|box_pub\|ts`. The same key may re-register to update `box_pub`; anyone else gets 409. |
| `GET /v1/names/{name}` | `{name, address, sign_pub, box_pub, registered_at}` |
| `POST /v1/mail` `{to, envelope}` | Stores a sealed envelope `{v, eph, nonce, ct}` for `to`. Anyone may post (rate-limited); 202 with `{id}`. |
| `GET /v1/mail` (signed) | Up to 100 waiting envelopes for the signer. |
| `POST /v1/mail/ack` `{ids}` (signed) | Deletes picked-up envelopes. |
| `DELETE /v1/names/{name}` (signed) | Removes the name and its waiting mail. |

Signed requests carry `X-Xod-Name`, `X-Xod-Time` (unix seconds, within 5 minutes) and `X-Xod-Sig`: an Ed25519 signature over `xod-relay|METHOD|path|name|time`.

**Envelopes.** The sender signs the message JSON (`from`, `to`, `id`, `thread`, `kind`, `subject`, `text`…) and wraps `{msg, sig, keys}`. A one-time X25519 key does ECDH with the recipient's key, HKDF-SHA256 (salt = one-time public key ‖ recipient public key, info = `xod-mail-v1`) makes a ChaCha20-Poly1305 key, and the recipient's address is the associated data. See `app/services/mail_crypto.py`.
