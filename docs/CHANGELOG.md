# Changelog

## 0.4.2

**Xod on Windows (preview)**
- A Windows installer (`Xod-<version>-x64-setup.exe`): per user, no admin, Start menu and desktop shortcuts, everything inside (its own Python and packages). It updates itself like the Mac app: it downloads the new installer, checks it against the release's checksums and installs it when you quit or press Update.
- Works on Windows: chats with LM Studio, Ollama or any cloud model; web research in its own browser (Chrome, or Edge, which every PC has); memory and the companion; files, terminal commands (in PowerShell), opening things in File Explorer, the Recycle Bin, the clipboard; building apps; background tasks and routines. API keys live in Windows Credential Manager, data in `%APPDATA%\Xod`.
- The setup screen on a PC offers what works there (LM Studio, Ollama, a cloud model), and Xod's instructions to its model talk about File Explorer, the Recycle Bin and PowerShell instead of Finder, the Trash and zsh.
- Mac only for now: the built-in model engine (MLX), on-device images, video and voice, and the Calendar/Notes/Reminders/Mail connectors.
- Every release now builds and tests Windows too: a clean Windows machine starts Xod from scratch and has a chat, then the installer is built, installed and started.

## 0.4.1

- Fixed: with a cloud model that thinks before answering (like the default one on Fireworks), a web answer could come back as only its thinking, with no answer. Xod now asks it to keep thinking short, leaves room for the thinking on top of the answer, and if a reply still ends with no answer it asks once more for just the answer.
- The header no longer says "AI offline" when your chats run on a cloud model and LM Studio is closed: it says "Ready · Fireworks" (or that the key is missing).

## 0.4.0

**A life of its own, told now and then**
- Xod's companion only reads up on what it isn't sure of, so every read can add something. It looks up questions from your chats it got corrected on, failed its own check on, or hedged on ("I'm not sure…"), taking only the general part and never anything about you. For the topics it follows, it also tests itself: it writes questions, answers each twice, and checks whether the answers agree. Anything about recent events counts as unsure, because it happened after the model was trained, however confident the model sounds. Things it already knows well are recorded and skipped.
- What it finds goes into its own notebook: the question, the answer from the pages, whether its earlier guess was wrong, the facts and the sources, and a follow-up question it's now unsure about.
- Chats use the notebook. Ask something it has read up on and you see a Notebook step, and it answers from what it found, saying when it read it.
- It no longer tells you everything. Most of what it finds stays in its notebook; it brings you something only when it's new, about something you care about, it hasn't told you anything for a few hours, and nothing it told you is still unread. Two a day at most by default (Heart → Its own time, 0 to 5). An old pile of unread postcards is cleared down to the newest one. "What are you thinking about?" still always gets an answer.
- It decides what it feels like doing each day: going deep on one topic, poking around, thinking about your projects, or taking it slow, from its drives (wants to learn, get better, tell you, rest) and from what went well before. Rewards are what you liked, what you brought up in chat yourself, and whether a read was actually new to it (compared with its notebook by meaning), so topics that pay off come up more often. Heart shows today's plan, the drives and the notebook.
- Ask "what have you been reading about?" and it answers from its notebook.
- Heart → Notebook shows what it's still unsure about and what it found. A switch under Its own time stops it looking into questions from your chats.
- Its own thoughts now use LM Studio when it's running here, even if your questions go to a cloud model (before, with a cloud model it only had canned thoughts). Never a cloud model.

**Understands follow-ups, learns from how answers went**
- A follow-up like "how much is the second one?" or "how tall is it?" is first rewritten as a full question ("How much does the Bose QuietComfort Ultra cost?"), shown in small text above the answer, and routing, memory, search and the planner all work from that. About 1–2 seconds on a local model.
- When you correct it ("no, that's wrong…"), it keeps the lesson and brings it up next time a similar question comes in, so it doesn't make the same mistake twice.
- Cleaner training data: every model call in a chat turn is tied to that turn and labelled by how it went. A correction or the same question asked again marks it bad; thanks marks it good; after each answer, when the Mac is idle, the local model checks whether the answer addressed the question, and "passed, and you moved on" marks it good. Nothing is guessed. The router's decisions to search or use tools are now recorded too (before, only its direct answers were). The Model Lab shows the counts.

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

**Knows you**
- Xod keeps a short sheet of what it knows about you (Settings → What Xod knows about you): your name, work, where you live, the people and pets in your life, what you're building, how you like answers. It's on this Mac, every fact says where it came from, and you can edit or forget any of it (or everything).
- It learns as you talk: tell it "I'm a chemistry teacher in Portland" and a small note under the answer says "Remembered: …" with Undo. "Remember that …" and "forget …" work too. It never keeps passwords, keys or card numbers, and you can switch learning off.
- "Get to know me from this Mac": pick what it may look at (your Mac account name, your Contacts card, Xod's job profile, git, time zone and language, your apps, your code projects) and review every fact before it's kept.
- What it knows is in every answer, so "what's my name?" just works, and the companion calls you by your name.
- It remembers what you've done together: every chat, every finished background task and routine run, and what the companion read up on, with dates. Ask "what was that ramen place you found last week?" or "what did we decide about the logo?" and it looks through its memory first (you see a Memory step), searching by words and by meaning, and understands "yesterday", "last week", "in September". The search runs on your Mac with a small local model, whatever model answers your questions.
- It knows its own work: "what are you working on?", "anything waiting on me?", "how did the Acme application go?" are answered from its task list (running, waiting for your OK, finished with their results, routines and when they run next, background jobs). The planner can also look up any task's full log and result.
- Facts that change keep their history: "moved to Denver" replaces "Lives in Austin", and "where did I live before?" still knows.
- Settings → What Xod knows about you can search your memory (click a result to open that chat) and has a switch to stop it using past chats.
- The three made-up "memories" new installs started with (one claimed a design taste you never stated) are no longer created or recalled.

**Feels faster**
- Web answers start in about 10 seconds instead of 25 (and finish in about 15 instead of 35) on a 12B model on a 16 GB Mac. The search starts the moment you ask, while Xod is still deciding whether it needs the web; pages are read in parallel and slow ones aren't waited for; and the model reads the question and each page as it arrives, so when the last page is in it can start writing almost right away.
- Xod gets ready while you type: it starts its browser and re-reads its own instructions if a long answer pushed them out of the model's memory, and does the same right after each answer. A plain question ("tell me a fun fact about otters") starts answering in about 2 seconds.
- Reader tabs skip images, fonts, video and ad trackers: pages load faster and use less memory.
- Browser tasks say what they're doing during long steps ("Double-checking everything before a click that sends…"), ask for a screenshot less often, and keep their careful checks short.

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
