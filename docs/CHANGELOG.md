# Changelog

## Unreleased

**Make Xod happy**
- Treats: every answer and build you finish together leaves a little ✦ stardust (up to 12 a day). Hover Xod and give one back: it flies over, Xod catches it, munches with puffed cheeks, gets heart eyes and does a little dance.
- Catch: a glowing star you drag and fling around the window, with real bounces. Xod chases it, catches it (streaks count, and it dances at 3 in a row), brings it back and asks for another throw.
- Wishes: now and then, when you're around and it's quiet, Xod asks for something small: a treat, a game, a pet, one good thing about your day, or to show you a trick. "Not now" is always fine; at most three a day.
- Keep petting and it giggles (a tickle); thank it in the chat and it blushes with heart eyes; it says good morning with a stretch; and once in a while it sneezes.
- Xod's heart has a new Treats & play section with your stardust and best catch streak.

**More life**
- How Xod feels changes how it acts: delighted means bouncier, more humming and a bigger smile; sleepy means droopy lids and slow habits; curious means wide eyes and looking around; a bit down means quieter.
- New habits: it juggles three little stars, reads a tiny book (eyes across the lines, a page turn), blows soap bubbles you can pop, and nods off when it's drowsy.
- Asleep, it dreams: a thought cloud with stars, notes, or one of your interests.
- It notices you: startles when the cursor whooshes past, wobbles when you resize the window, follows your scrolling, perks up when you send a message, says ✓ when you copy an answer, and is proud after a long one.

**Reads the web in its own browser**
- Web questions are answered from the pages themselves, not two-line snippets: Xod searches Google in its own browser (DuckDuckGo if Google shows a bot check), opens the best few results side by side in background tabs, keeps the passages that answer you, and cites them. Pages that build themselves with JavaScript work, and cookie banners, menus and footers are left out. No search API key needed. A question like "who won the World Cup final?" takes about 5–8 seconds of searching and reading.
- Each answer shows the sites it read under the search, as links.
- Reading a single page ("summarize this link") also goes through the browser first, so JavaScript sites come through.
- Xod's companion reads up on your interests the same way, in its own browser, and that's on by default again. Its thoughts about what it read still only use the model on your Mac.

**Faster, smarter browser tasks**
- Browser tasks take about half the time: comparing a price across two shops went from 2 min 24 s to 1 min 16 s, and adding a size to a cart from 3 min 44 s to 1 min 22 s, on the 12B local model.
- It does several things per step: fill every field it knows and press Continue in one go, instead of asking the model again for each field. If the page shifts under it, the remaining actions find their element again or stop.
- Shorter replies from the model (only what the step needs), a page description it can reuse from one step to the next, and screenshots only when they help (the model asks for one, something failed, or the page has almost nothing to click).
- Form fields are described by their real labels, and links no longer flood the page text with URLs.
- A new "extract" step reads a whole long page for one thing (a price, a date, a list) at once.
- Pages are ready as soon as they've loaded and stopped changing, instead of a fixed wait on every page.
- After you approve a submit, it remembers what it already filled in instead of typing it again.

**All local**
- Xod's own life stays on your Mac: its thoughts only ever use the model on this Mac (never a cloud model, even if one is set up for your questions). With no local model it still writes simple thoughts of its own, without any model.

**Voice mode**
- It no longer answers background noise or made-up words. A stronger on-device model (Whisper large-v3 turbo) hears you better; transcripts Whisper itself doubts are dropped ("no, no, no…", one sentence over and over, "Thanks for watching!" into silence); sound with no speech isn't transcribed at all; and it learns how loud you are and ignores much quieter voices like a TV across the room.
- It waits for the rest when you trail off on "and…" or "so the…", and asks you to repeat when it isn't sure what it heard.

## 0.3.1

**Update with one click**
- The bottom of the sidebar always shows your version: "Xod 0.3.1 · up to date" (with a button to check now), or "Xod 0.4.0 is out" with an **Update** button. Xod checks GitHub when it starts, every hour, and when you come back to it after a while.
- A new version downloads in the background (with a progress bar), is checked against the release's checksum and signature, and **Update** restarts into it. Don't want it yet? It installs when you quit. The home screen shows the same Update button.
- Xod → Check for Updates… does the same from the menu.

## 0.3.0

**Updates itself**
- When a new version is out, Xod downloads it in the background, checks it (the release's SHA-256 checksum, the app's version and signature), and asks "Restart now / Later"; Later installs it when you quit. Your chats, projects and settings aren't touched. Xod → Check for Updates… checks right away, and "Tell me about new versions" in Settings turns it off. (0.2.0 and earlier can't do this: install 0.3.0 by hand once.)

**Xod's inner life**
- The companion has a mood (joy, energy, curiosity, calm), a bond with you that grows when you work together, say thanks or give it a pet, and it grows up through five stages (Hatchling, Sprout, Companion, Kindred, Luminary), each with its own glow, and a moment when it happens.
- It learns from you: your name if you share it, whether you like short or detailed answers, a bit of fun, the topics you keep coming back to, and the corrections you give it. A short note from this shapes its answers.
- When you're idle it leaves its spot and wanders the whole window, peeking at your projects and thinking out loud. When you're away for a while (plugged in, a few times a day at most), it does things for itself: reads up on one of your interests (read-only), thinks of an idea for your projects, or daydreams. You get a postcard when you're back; "Thanks!" or "Not my thing" is the reward it learns from.
- Tools → Xod's heart shows all of it: stage, bond, mood, what it learned (forget any of it), its journal, and switches for its own time. It's a character, not a person; it never guilt-trips, its bond never drops because you were away, it never changes your files or accounts on its own, and everything stays on your Mac.

**Do things in your apps**
- "Move my dentist appointment to Friday at 3", "check off milk", "delete the note about the old wifi": Xod finds the item first, then changes it, step by step, and follow-ups like "add bread too" keep going.
- This Mac can now change and delete, not just add and read: update and delete Calendar events, update, check off and delete Reminders, append to or rewrite and delete Notes, mark and trash Mail.
- Deleting always waits for your OK, even on a connector you've set to fully trusted.
- "Launch Spotify" opens Mac apps by name.

**Launch what it builds**
- "Launch it" opens an app Xod built in its own window in the desktop app (sandboxed, no access to Xod), or in your browser. "Test it" runs the error check.

**Build apps and games**
- Ask for an app or a game ("make me a 3D game where you dodge falling cubes", "build a pomodoro timer") and Xod writes it for real: it plans the files, writes each one in full, runs it in a headless browser (pressing a few keys like a player), and fixes what breaks before telling you it's done. The answer lists only files that are really on disk.
- A preview pane opens beside the chat, like an IDE's: the app running live, its Files with the code, and a Console with its logs and errors (with "Ask Xod to fix these"). Resize it, switch to phone size, open it in your browser, or collapse it to a tab on the edge.
- Ask for changes in the same chat ("make it harder", "add a high score"): Xod makes small, exact edits instead of rewriting files, then runs it again. Every write is in Activity with Undo.
- 3D works offline: three.js ships with Xod. Ask for sprites, textures or backgrounds and Xod paints them with Media Studio when image generation is set up.
- Apps live in a folder of the project you're in, and show under it on the left; outside a project, Xod makes one.
- Games use three.js unless you ask for something else, and "like Call of Duty" means that game's core loop in miniature: Xod plans a checklist of must-haves (enemies that fight back, live health and ammo, waves, game over and restart) and builds all of it in one go, then opens it to play.
- "Make a new folder in Documents for it" (or Desktop, Downloads) puts the app there. "Let's make a video game" and "I want a racing game" start a build too.
- Move it around by asking: "move it to my Desktop", "into a folder called Games", "rename it", "copy it", "zip it up", "show it in Finder", "open main.js in VS Code", "open it in Chrome". "Delete the game" asks first, then moves it to the Trash. Each change is in Activity with Undo, and follow-ups keep working from the new place.
- Previews are sandboxed: they run with no access to Xod, your files or its API, from a secret address that changes each time Xod starts. localStorage works and is kept in the app's folder.

**Find jobs and apply**
- "Find me FDE jobs" now returns real, open roles instead of a web summary: Xod expands job acronyms (FDE is Forward Deployed Engineer, plus SWE, SRE, MLE, TPM and more), finds the companies hiring for it on Ashby, Greenhouse and Lever, and reads their public job boards for title, location, remote/hybrid/on-site, pay and a direct apply link.
- A jobs card in the chat lists them with filters and Apply buttons. Follow-ups narrow the same list ("only remote", "in New York", "senior", "not at Google") and "apply to 2" means the second one you see.
- A job profile (from the jobs card, or asked for when you first apply) holds your name, email, résumé and answers, on your Mac only (readable just by you).
- Each application is a task in Xod's browser: it fills the form from your profile, uploads your résumé, answers questions, and stops for your OK before submitting. If the page asks to confirm, that becomes the same approval instead of a question.
- Xod's browser now uses its own port per data folder, so a test or second copy can never take over your signed-in browser.

**Fixes**
- Starting Xod no longer asks for your Keychain password over and over. Every API key, connector token and identity key was its own Keychain item, and macOS asks for each one; now they share one item, read once per launch. Click "Always Allow" once and it stops asking. Older items move over on their own the first time they're needed.
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
