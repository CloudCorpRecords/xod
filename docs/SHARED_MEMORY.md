# Shared memory across your Xods

Goal: what you tell one of your Xods, typed on that computer or said from your paired phone, is known on your
other Xods too. Today each computer remembers on its own: chats in `xod.db`, the search index in `recall.db`,
facts about you in `about_you.json` (`user_memory.py`), notes in `chroma`.

## 1. What is shared

| Shared | Not shared, ever |
|---|---|
| Facts about you (`user_memory`): the sheet in "About you", including its "past" timeline | Chat messages, files, attachments, screenshots |
| Forgets: "forget my phone number", "forget everything" | The search index (`recall.db`), notes (`chroma`), the workspace index |
| (Later, its own switch, off) a one-paragraph summary per chat | Settings, keys, passwords, connectors, Agent Mail, tasks |

Facts are already short, checked against `SECRET_RE` and meant to be read by the model on every request, so they are
the smallest useful thing to share. Chat summaries are a later step with their own switch: v1 ships facts only.

## 2. Off by default, and opt-in on both ends

- New setting `memory_sync`, default `off`, under Settings → Your Xods → "Share what Xod knows about you with your
  other computers".
- Turning it on lists your other Xods (`xod_network.find()`, same account only). Sharing with a computer starts only
  after an approval card on **each** side ("Share what Xod knows about you with Laptop?"), the same kind of card a
  handed-over task makes, so it can be approved from the phone too.
- Turning it off stops sending and receiving at once. Facts already received stay unless you press
  "Remove what came from other computers".

## 3. Transport: the Xod network, sealed like Agent Mail

- Same beacon as handed tasks (`xod_network`, port 8791, bound to the Tailscale address only). New endpoints:
  - `POST /v1/memory/hello` — the computer's sync keys (sign + box public keys), asks for the approval card.
  - `GET  /v1/memory/changes?since=<seq>` — changes after a cursor, as one sealed envelope.
- Tailscale already encrypts the link. On top, every change batch is sealed and signed with `mail_crypto`
  (X25519 → HKDF-SHA256 → ChaCha20-Poly1305, Ed25519 signature), so a reply is only readable by the computer that
  asked and only accepted from the keys pinned when you approved it. Each Xod gets its own sync identity in its data
  folder (`memory_sync_key`); it does not need Agent Mail to be set up.
- Pull, not push: every 2 minutes while on (and right after a local change, as a nudge), each Xod asks each paired,
  online Xod for changes since its cursor. A computer that was off catches up when it comes back.

## 4. Who is trusted: your account only

Every request is checked twice:
1. `tailscale whois` on the caller (as `receive_task` does today): same Tailscale account, or 403.
2. The caller's keys match the ones pinned at approval. New keys (reinstall) stop sharing with that computer and
   show "Laptop has new keys: share again?" instead of syncing.

Shared machines on the tailnet, other people's devices and anything outside Tailscale are refused. Nothing opens to
the internet.

## 5. Changes, conflicts and deletes

Each Xod keeps a small change log next to `about_you.json` (`about_you_changes.json`): one entry per add, edit,
forget and "forget everything", with a local sequence number (the cursor others pull by).

- **Identity of a fact:** its existing id (`f` + random hex), plus two new fields: `origin` (which computer first
  kept it) and `via` (`chat`, `phone`, `mac` for "Get to know me", `you edited`).
- **Same fact edited on two computers:** newest `updated` wins; ties go to the origin name, so every computer picks
  the same one.
- **One-per-key facts** (name, city, job, ...): two different facts for the same key keep the newest; the other goes to
  the "past" timeline, exactly as `add()` does locally.
- **Same fact said twice** (two ids, matching text by `_same`): merged into the older id, so it isn't listed twice.
- **Forget:** a tombstone `{id, deleted_at}` is shared and kept 90 days; a fact older than its tombstone is never
  brought back by a computer that was offline.
- **Forget everything:** shared as a `cleared_at` mark; every computer drops facts last updated before it.
- **Received facts are checked again** (`SECRET_RE`, length, topic and key from the known lists) before they are kept,
  and are used in prompts as facts, the same as local ones.

## 6. Seeing and undoing what was shared

- "About you": each fact shows where it came from: "you said, on Laptop", "from your phone, via Desktop".
- Settings → Your Xods → Shared memory: the computers you share with, last sync, and a short log
  ("Got 3 facts from Laptop · 10:42"). Per computer: "Stop sharing" and "Remove what came from Laptop" (removes
  them here only; it is not a forget everywhere).
- Undo: each received batch keeps the previous versions of what it changed for 30 days; "Undo" on a log line puts
  them back here (a normal local edit, so it then syncs like any edit).
- Forgetting a fact here forgets it on all your shared computers; the card says so before it does it.

## 7. Phone chats get a source

- `sessions` gets a `source` column (`desktop` by default; a migration fills old rows).
- `phone_api._chat` sends `source="phone"` with the chat request, so chats started from the phone are tagged; the
  phone's chat list and the desktop sidebar can filter by it ("From your phone").
- Facts learned from a phone chat get `via: "phone"`, which is how "from your phone, via Desktop" is shown.
  This part does not depend on sync being on.

## 8. Build plan, after this design is agreed

1. `sessions.source` + phone tag + filter (small, ships on its own).
2. `user_memory`: `origin`/`via` fields, change log, tombstones, `merge(changes)`.
3. `memory_sync.py`: sync identity, pairing via approval cards, pull loop, beacon endpoints in `xod_network`.
4. UI: setting, paired computers, log, per-fact source, undo.
5. `tests/smoke/run_memory_sync_test.py`: two data folders in one process with a stubbed `whois`, covering
   off by default; refused when the account differs or keys change; add / edit / conflict / one-per-key /
   duplicate merge; forget and forget-everything reaching an offline computer; secrets refused on receive;
   undo; phone chats tagged `source="phone"`.

Open choices for you:
- Facts only in v1 (recommended), or chat summaries too?
- Should a forget on one computer forget everywhere (recommended, with a warning on the card), or only locally?
