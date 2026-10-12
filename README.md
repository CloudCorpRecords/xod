<p align="center">
  <img src="docs/media/moon.gif" width="420" alt="Xod, a small moon, looking around and smiling">
</p>

<h1 align="center">Xod</h1>

<p align="center">
  <b>A private AI that works on your Mac.</b><br>
  It researches, works with your files and runs jobs in the background, and it asks before it acts.
</p>

<p align="center">
  <a href="https://github.com/CloudCorpRecords/xod/releases/latest"><b>Download for Mac</b></a> ·
  <a href="docs/media/xod-launch.mp4">Watch the 2-minute film</a> ·
  <a href="#build-from-source">Build from source</a>
</p>

<p align="center">
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-f2b24a?style=flat-square"></a>
  <img alt="Apple silicon" src="https://img.shields.io/badge/Mac-Apple%20silicon-0f1222?style=flat-square">
  <img alt="Runs locally" src="https://img.shields.io/badge/runs-on%20your%20Mac-93d17f?style=flat-square">
  <img alt="No telemetry" src="https://img.shields.io/badge/telemetry-none-a196ec?style=flat-square">
</p>

<br>

![Xod answering a research question, with sources](docs/screenshots/answer.png)

## See it in action

| | |
|---|---|
| **Talk to it, hands-free** ([watch with sound](docs/media/xod-conversation.mp4), 1 min) | **Watch it work** ([2-minute film](docs/media/xod-launch.mp4)) |
| <img src="docs/media/conversation.gif" width="420" alt="Saying Hey Xod, asking about tomorrow's weather in Tokyo, hearing the answer and asking a follow-up"> | <img src="docs/media/powers.gif" width="420" alt="The owl companion flying to a web search, peeking at a browser snapshot, waiting by an approval and writing the answer"> |

All demos are recorded from the real app with a throwaway demo data folder. In the conversation video, long thinking pauses are shortened and labelled; speech is never sped up.

## Hand it a job

- **"Every weekday at 8, give me the five biggest tech stories with sources."** Save it once as a routine; the report is waiting for you.
- **"Find the junior Python roles on these three job boards and fill in the applications."** Xod works through the sites in its own browser and stops for your OK before each submit.
- **"Tidy my Downloads folder and tell me what's taking the space."** It sorts the files, shows exactly what it moved, and one click puts everything back.

## Why Xod

|  |  |
|---|---|
| **Private by default** | Chats, files, voice and images stay on your Mac. Use Xod's built-in engine, LM Studio or Ollama. No account, no telemetry. |
| **It does things** | A real browser, your files, the terminal, background tasks, and a task inbox that keeps working while you're away. |
| **You stay in charge** | It stops for your OK before anything is sent, paid, posted or deleted. Every action is logged, and file changes can be undone. |
| **Builds what you ask for** | Apps and games written, run, tested and fixed on your Mac, with a live preview beside the chat. |
| **A companion that grows** | Moods, a bond with you, five stages to grow through, and a journal of what it thought about while you were away. |
| **Talk to it** | Say "Hey Xod" and have a spoken back-and-forth, hands-free. It answers out loud, takes follow-ups and stops when you talk over it, all on your Mac. |
| **Teach once, repeat forever** | Turn any chat into a routine. Xod remembers the steps that worked and runs it again, on a schedule if you like. |
| **Plays well with others** | One-click connections to your Mac's Calendar, Notes, Mail and Reminders, Notion, Linear and GitHub, plus any MCP tool. Claude Desktop, Cursor and others can hand work to Xod. |
| **Cloud only when it helps** | Optionally pass the hard steps to Claude, GPT or Gemini, with your permission, the cost shown, and a monthly budget. |

## Install

1. [Download `Xod-0.2.0-arm64.dmg`](https://github.com/CloudCorpRecords/xod/releases/latest) (about 410 MB) and open it.
2. Drag **Xod** onto **Applications**.
3. Open Xod. Everything it needs is already inside, so it's ready in seconds; a short setup helps you pick a model.

| Your Mac | Suggested model | Download |
|---|---|---|
| 8–16 GB | Qwen 3.5 4B (built-in engine): a researched answer in about 10 seconds | 3.1 GB |
| 24 GB | Qwen 3.5 9B (built-in engine), or any model you already use in LM Studio or Ollama | 6 GB |
| 32 GB or more | Qwen 3.8 27B (built-in engine) | 16 GB |

> Xod isn't notarized by Apple yet, so the first time you open it macOS says it can't check it. Click **Done**, open **System Settings → Privacy & Security**, scroll down and click **Open Anyway** next to Xod. You only do this once.

Needs a Mac with Apple silicon (M1 or newer), macOS 13 or later, and Google Chrome for Xod's browser.

## A quick tour

**Meet the moon, or the cat, the ghost, the robot, the owl or the sprout.** Xod's companion reads along as you type, listens when you talk, scans pages while it searches, talks while answers arrive, and dozes off in a nightcap when you're away. Each look has its own habits: the cat's tail swishes, the ghost vanishes and says boo, the robot's light shows what it's doing, the sprout blooms when it's happy. Pet it, drag it, double-click for a flip, and right-click to change its look.

<p align="center"><img src="docs/media/looks.gif" width="720" alt="Six looks for Xod: moon, cat, ghost, robot, owl and sprout, each doing its own little trick"></p>

While Xod works, its companion leaves its spot by the reply and does the work where you can see it: it conjures each tool as it starts and taps it when it's done, peeks over browser snapshots, writes the answer with a quill that follows the words, lassoes anything that needs your OK and waits by it, then signs the answer and flies home. Each look travels its own way: the moon flies, the cat pounces, the ghost phases, the robot zips, the owl flaps and the sprout hops. Opening a panel takes a little spell, and on the home screen it sometimes lifts a card just to see if it can.

<p align="center"><img src="docs/media/powers.gif" width="640" alt="The owl companion flying to a web search, peeking at a browser snapshot, waiting by an approval and writing the answer with a quill"></p>

**Routines.** Jobs Xod repeats for you, by hand or on a schedule. Each run lands in Tasks and still asks before sending or paying.

![Routines](docs/screenshots/routines.png)

**Activity and undo.** Everything Xod did, in plain words. File writes, edits, moves and trash can be undone, and Xod won't overwrite a file you've changed since.

![Activity](docs/screenshots/activity.png)

**Shareable reports.** Turn any answer into a polished page and PDF with numbered sources.

![A report](docs/screenshots/report.png)

**Agent Mail (preview).** An optional add-on: give your Xod an address like `sam@post.example.com` and it can message other agents: ask a friend's agent for help, hand a job to your Xod at work, or connect a few agents into a team. Messages are end-to-end encrypted; the relay in the middle can't read them, doesn't know who sent them, and deletes each one once it's delivered. Xod never sends anything or runs a requested task without your OK. Run your own relay with [relay/](relay/README.md).

**Connected apps.** Ask in plain words ("move my dentist appointment to Friday", "check off milk", "delete that note") and Xod finds the item, then changes it; deleting always waits for your OK. One click connects This Mac (Calendar, Reminders, Notes and Mail, including Google and iCloud accounts you've added to macOS), Notion and Linear (sign in with your account) and GitHub. Any other MCP server works too; secrets go to your Keychain. Copy the snippet to use Xod from Claude Desktop, Cursor or Claude Code.

![Connected apps](docs/screenshots/connected-apps.png)

**Add-ons.** Optional packs for startup and market intel, people lookup, website forensics, creators and trends, Media Studio and Model Lab.

![Add-ons](docs/screenshots/add-ons.png)

### Build apps and games

<p align="center"><img src="docs/screenshots/builder.png" width="760" alt="Xod built a 3D game from one sentence; it's running in the preview pane beside the chat"></p>

Ask for something you can use or play, like **"make me a 3D game where you dodge falling cubes"** or **"build a pomodoro timer"**, and Xod writes it for real:

- **It writes it, runs it and fixes it.** Xod plans the files and writes each one in full. Then it opens the app in a hidden browser, plays it for a few seconds and fixes any errors before telling you it's done. The answer lists only files that really exist.
- **A preview beside the chat.** The app runs in a pane next to the chat, with its **Files** and a **Console** that shows errors, plus an "Ask Xod to fix these" button. Switch it to phone size, open it in your browser, or collapse it to a tab on the edge.
- **Change it by asking.** Say "make it harder" or "add a high score" and Xod edits just the lines that need it, runs the app again, and keeps every change in Activity with Undo. Say "launch it" to open it in its own window.
- **3D and art included.** three.js is built in and works offline. Ask for sprites or textures and Xod paints them with Media Studio.
- **Safely fenced off.** Previews run sandboxed, with no access to Xod, your files or its API. Each app lives in a folder of your project and shows under the project on the left.

### Xod's heart

<p align="center"><img src="docs/screenshots/heart.png" width="760" alt="Xod's heart: its stage, bond, mood, what it learned about you, and its journal"></p>

Xod's companion has a little life of its own. It has moods, a bond with you that grows as you work together, and it grows up through five stages (Hatchling, Sprout, Companion, Kindred, Luminary). When you're idle it leaves its spot and wanders around the window, thinking out loud. When you're away for a while it reads up on something you care about, thinks of an idea for one of your projects, or daydreams, and brings you a postcard when you're back. What you thank it for is how it learns what you like.

It learns from you too: your name, whether you like answers short or detailed, the topics you keep coming back to, and what you correct. **Tools → Xod's heart** shows all of it, and you can make it forget anything. It's a character, not a person: it never guilt-trips, its bond never drops because you were away, it never touches your files or accounts on its own, and everything stays on your Mac.

### Talk to it

<p align="center"><img src="docs/media/conversation.gif" width="640" alt="A hands-free conversation with Xod: the question, the spoken answer with live captions, and a follow-up"></p>

Say **"Hey Xod, what's the weather tomorrow?"** in one breath, click the mic, or press **⌃⌥Space** from any app, and just talk. [Watch it with sound.](docs/media/xod-conversation.mp4)

- **A real back-and-forth.** Xod answers out loud in a sentence or three and keeps the details and sources on screen. Then it keeps listening, so a follow-up needs no wake word. After a quiet pause, or "thanks, that's all", it says goodbye.
- **Interrupt it like a person.** Talk over it and it goes quiet straight away. A new question while it's still thinking replaces the old one. "Stop", "say that again" and "never mind" work too.
- **No dead air.** A short "let me look that up" while it searches, live captions as you speak, and a companion that bobs along with your voice, talks in sync and chimes in its own voice for each look.
- **Spoken replies anywhere.** Have Xod read answers aloud when you talk, every time, or never, and press **Listen** on any reply.
- **On your Mac.** Whisper turns speech into text on your Mac and Xod speaks with your Mac's own voices; nothing is recorded or sent. Listening for "Hey Xod" stays off until you turn it on under the speaker button, and voice commands that would move files go through the agent, where they can be undone.

Also inside: images and video (FLUX, AnimateDiff), long-term memory, projects, a health check that fixes common problems, and a gated way for Xod to improve its own code, with tests, your approval and one-click revert.

## Privacy and security

- **Your data stays on your Mac** in `~/Library/Application Support/Xod`, readable only by your account. Set `XOD_DATA_DIR` to keep it elsewhere.
- **Keys and logins live in the macOS Keychain**, never in files, logs or prompts.
- **One small network check:** once a day Xod asks GitHub whether a newer version exists (nothing else is sent). Switch it off in Tools → What's coming next, or with `XOD_NO_UPDATE_CHECK=1`.
- **Local only.** The server listens on `127.0.0.1` and refuses requests from websites, so a page you visit can't drive Xod.
- **Untrusted text stays text.** AI output is sanitized, the UI runs under a strict Content-Security-Policy, and every UI library is bundled at a pinned version.
- **Audited dependencies**, re-checked weekly in CI.

Found a security problem? Please report it privately; see [SECURITY.md](.github/SECURITY.md).

## Build from source

```bash
git clone https://github.com/CloudCorpRecords/xod.git
cd xod
./run.sh                      # sets up .venv the first time, then serves http://localhost:8000
```

The desktop app: `cd electron && npm install && npm start`. The installer: `npm run dist`.

```
xod/
├── app/          FastAPI server, agent, services and the web UI
├── electron/     the Mac app and its installer
├── relay/        the Agent Mail post office (Python or Cloudflare Worker)
├── docs/         roadmap, changelog, screenshots and media
├── scripts/      release checks, license notices, screenshot and video tools
└── tests/        end-to-end suites (they use a throwaway data folder, never yours)
```

Start with [CONTRIBUTING.md](.github/CONTRIBUTING.md). Ideas and bugs are welcome in [Issues](https://github.com/CloudCorpRecords/xod/issues); see the [roadmap](docs/ROADMAP.md) for what's next.

## License

[MIT](LICENSE). Xod stands on a lot of great open-source work; the Mac app includes the notices for everything it bundles (`THIRD_PARTY_LICENSES.txt`, made by `scripts/third_party_licenses.py`).
