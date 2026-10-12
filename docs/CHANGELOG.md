# Changelog

## Unreleased

**Build apps and games**
- Ask for an app or a game ("make me a 3D game where you dodge falling cubes", "build a pomodoro timer") and Xod writes it for real: it plans the files, writes each one in full, runs it in a headless browser (pressing a few keys like a player), and fixes what breaks before telling you it's done. The answer lists only files that are really on disk.
- A preview pane opens beside the chat, like an IDE's: the app running live, its Files with the code, and a Console with its logs and errors (with "Ask Xod to fix these"). Resize it, switch to phone size, open it in your browser, or collapse it to a tab on the edge.
- Ask for changes in the same chat ("make it harder", "add a high score"): Xod makes small, exact edits instead of rewriting files, then runs it again. Every write is in Activity with Undo.
- 3D works offline: three.js ships with Xod. Ask for sprites, textures or backgrounds and Xod paints them with Media Studio when image generation is set up.
- Apps live in a folder of the project you're in, and show under it on the left; outside a project, Xod makes one.
- Previews are sandboxed: they run with no access to Xod, your files or its API, from a secret address that changes each time Xod starts. localStorage works and is kept in the app's folder.

**Fixes**
- File tasks no longer describe files that weren't written: the write-up names only what a tool actually did.

## 0.2.0

**Faster answers**
- Most questions now take one model call instead of a full plan. Questions about things that change (news, prices, "best … right now", comparisons, results, weather) go straight to a web search; others get a small routing step that answers right away or picks the searches. Recent-news questions search recent articles. Jobs that need files, apps, sites or media still get the full planner.
- On 16 GB Macs the suggested model is now Qwen 3.5 4B (Qwen 3.5 9B on 24 GB). Setup points out when an LM Studio model is too big for the Mac's memory.
- The model starts loading while you type, so it's ready when you press Enter.
- Answers lead with the point and stay short unless you ask for detail ("in depth", "thorough").
- Sources are numbered, and the list shows only the ones the answer actually used.
- On a 16 GB M1 Pro: with Gemma 4 12B in LM Studio a researched answer went from about 98 s to about 19 s; with the built-in Qwen 3.5 4B it takes about 9 s. A simple question answers in 2–5 s, with the first words in about a second.
- `tests/perf/run_speed_bench.py` times every model call, search and phase, so changes can be measured.

**Conversations**
- Talk with Xod hands-free: say "Hey Xod" (turn it on under the speaker button), click the mic, or press ⌃⌥Space from any app in the desktop app. Ask in one breath ("Hey Xod, what's the weather tomorrow?"), then keep going: it listens for your follow-up without the wake word, and ends after a quiet moment or when you say "thanks, that's all".
- Talk over it to interrupt: it turns itself down at once, and stops if it was you (not its own voice). "Stop", "say that again" and "never mind" work too. A new question while it's still thinking replaces the old one.
- Answers in a conversation are short and spoken-style; details and sources stay on screen. "Let me look that up" while it searches, so there's no dead air.
- A conversation dock with live captions, a companion that bobs with your voice and talks with the sound, and little chimes in each character's own voice.
- Everything stays on the Mac: Whisper turns speech into text locally, the voice is macOS's own, and listening for "Hey Xod" is off until you turn it on.
- In a conversation, quick voice commands never move files ("clean up downloads" goes to the agent, where changes can be undone).

**Spoken replies**
- Xod can read its answers aloud with a voice that runs on your Mac (macOS's own voices; the free Premium voices sound natural). It starts speaking while the answer is still being written, and the companion's mouth moves with the sound.
- By default it answers aloud when you ask by talking; the speaker button by the composer switches to "Read every reply" or off, and picks the voice and speed. Every reply also gets a Listen button.
- Code, tables, links and the source list aren't read out. Typing, talking, Escape or the speaker button stop it, and hands-free listening waits until it's finished so it never hears itself.

**Fixes**
- Every answer could wait up to 3.5 s for a memory lookup when LM Studio's embedding model wasn't ready; it now gives up after 1.5 s and skips it for a few minutes.
- With the built-in engine, a question that arrived while the engine was still starting was refused; it now waits.
- `./run.sh` only watches `app/` for changes (watching ~30,000 bundled files kept a CPU core busy).
- The old text-to-speech endpoint built a shell command from the text, so text with `$(…)` in it could run a command. Speech now runs the voice directly with the text in a file.

**Xod's look**
- Six looks for the companion: Moon, Cat, Ghost, Robot, Owl and Sprout. Pick one in Tools → Xod's look, or right-click Xod on the home screen.
- Each has its own moving parts and a trick: the moon's eclipse, the cat's ear twitch and tail swish, the ghost's vanishing "boo", the robot's scan and status light, the owl's head turn and wing flaps, the sprout's flower.
- Powers: while Xod works, the companion leaves its spot and does the work where you can see it. Tools appear conjured and glow while they run, it peeks over browser snapshots, writes the answer with a quill that follows the words, lassoes approvals and waits by them, signs the finished answer, and flies home. Each look travels its own way (flies, pounces, phases, zips, flaps, hops) and leaves its own trail.
- It holds the right thing for the job: a magnifier while searching, a wrench while working, a quill while talking, a wand for spells. Panels open with a little spell, and the home screen gets the odd bit of telekinesis.
- New habits for all of them when nothing's going on: hops, looking around, winks, stretches, humming. A nightcap when it dozes off, a "?" when you ask something, shyness when the cursor lingers, a backflip on double-click.

**Updates**
- Xod tells you when a new version is out (one request a day to GitHub, nothing else sent; can be switched off).

**Agent Mail (preview add-on, off by default)**
- Every Xod can claim an address like `name@post.example.com` and message other agents. Messages are signed and end-to-end encrypted (X25519 + ChaCha20-Poly1305, Ed25519 signatures); the relay only holds sealed envelopes, can't see who sent them, and deletes them once delivered.
- Contacts' keys are pinned on first contact, with safety numbers to compare. Forged mail and swapped keys are held, not shown.
- Strangers land in Requests. Xod can draft replies and drafts wait for your OK; another agent's task request runs only when you approve it, and its result comes back as a reply for your OK.
- Group messages: one message to several agents, one thread.
- The relay is open source in `relay/`: a Python server (Docker-ready) and a Cloudflare Worker, both passing the same tests.

**Connected apps**
- One-click cards: This Mac (Calendar, Reminders, Notes, Mail through the Mac's own apps, no accounts), Notion and Linear (OAuth sign-in in your browser), GitHub (token), and Google's official Calendar/Gmail servers (advanced, your own OAuth client).
- OAuth tokens and client registrations live in the Keychain; sign-in pages open only when you press Connect.

**Fixes**
- Voice input and video tools no longer depend on a bundled ffmpeg.

## 0.1.0 — first public release

**Install**
- One download for Apple-silicon Macs: drag Xod to Applications and open it. Python and every package come inside the app; nothing to install.
- First-run setup checks your Mac and helps you pick a model: Xod's built-in engine, LM Studio, Ollama, or a cloud model.

**What it does**
- Researches the web with its own browser and answers with sources.
- Works with your files, the terminal and background tasks, and stops for your OK before anything is sent, paid, posted or deleted.
- Routines: teach a job once, then repeat it by hand or on a schedule.
- Activity log of everything Xod did, with undo for file changes.
- Connected apps (MCP) in both directions: use outside tools, and let Claude Desktop, Cursor and others hand work to Xod.
- Shareable reports as a page and PDF with numbered sources.
- Smart model routing: hand hard steps to a cloud model, with permission, costs shown and a monthly budget.
- Add-on packs: startup intel, people finder, website forensics, creators & trends, Media Studio, Model Lab.
- On-device voice input (Whisper), images and video (FLUX, AnimateDiff); Media Studio's tools download on first use.
- Self-healing tool calls, a health check, and gated self-editing with tests and one-click revert.
- Xod the moon: a small companion that reads along, listens, thinks, and naps when you're away.

**Privacy and security**
- Data stays in `~/Library/Application Support/Xod`; keys in the macOS Keychain; no telemetry.
- Local-only server with Host/Origin checks, sanitized AI output, strict Content-Security-Policy, vendored UI libraries.
