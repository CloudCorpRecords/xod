# ⚡ XOD — The Autonomous Intelligence & Computer Action Agent

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9+-green.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688.svg)](https://fastapi.tiangolo.com)
[![100% Local Capable](https://img.shields.io/badge/Local%20AI-LM%20Studio%20%7C%20Ollama-purple.svg)](https://lmstudio.ai)

**XOD** is an autonomous, embodied AI intelligence agent designed to run **locally on your machine** with a reactive web UI. Xod combines multimodal perception, an isolated Chrome browser sandbox, host system controls, workspace code intelligence, and live multi-source web research into a unified autonomous execution loop.

Xod is built to work **100% locally and offline** using local models via [LM Studio](https://lmstudio.ai) or [Ollama](https://ollama.com), or with leading cloud providers (Fireworks AI, OpenAI, Anthropic, Google Gemini, Groq, OpenRouter).

---

## 🌟 Core Pillars & Capabilities

### 1. 🌐 Autonomous Chrome Browser Sandbox
* **Direct CDP Control (Port 9228)**: Interacts with an isolated Chrome instance via the Chrome DevTools Protocol.
* **Set-of-Marks (SoM) Visual Tags**: Numbers visible interactive DOM elements (`[1]`, `[2]`, ...) so the agent can click, type, and navigate with high precision.
* **Embodied Form Automation**: Auto-detects inputs, checkboxes, and selects, filling forms in a single turn.
* **Smart Overlay Dismissal**: Automatically clears cookie banners, GDPR consent prompts, and newsletter popups.
* **Headless & Visible Desktop Modes**: Toggle between background headless execution and visible desktop Chrome so you can observe live or solve CAPTCHAs.
* **Page Exporting**: High-resolution screenshots and full-page PDF exports.

### 2. 💻 Host Computer Control & Telemetry
* **Background Shell Execution**: Runs terminal commands in persistent working directories without stealing UI window focus.
* **Port Listener Inspections**: Probes active listening TCP ports (3000, 8000, 5432, 9228) and identifies bound process PIDs.
* **System Telemetry**: Real-time monitoring of CPU load, RAM pressure, disk usage, and background daemons.
* **Fast Spotlight Discovery**: Instant Mac-wide file and directory search via Spotlight (`mdfind`).
* **Safe File Operations**: Move files, create directories, or safely send items to macOS Finder Trash (fully recoverable).

### 3. 📁 Controlled Workspace & Filesystem Agency
* **Project Code Inspection**: Explores directory trees, reads source files, and audits configurations (`package.json`, `pyproject.toml`, `.env`).
* **Symbol & Keyword Grep**: Searches codebases for symbols, classes, functions, and TODOs.
* **Folder Creation & File Organization**: Creates target directories and batch organizes files into folders on command.

### 4. 🧠 True Conversational Memory & Multi-Turn Synthesis
* **Preserved Conversational Context**: Multi-turn history is preserved and formatted into final synthesis so the agent never suffers amnesia across follow-ups.
* **In-Memory Follow-Up Fast-Path**: Follow-up formatting requests (*"summarize that in a table"*, *"compare those two"*, *"explain point 2"*) are synthesized directly from prior context without launching redundant web crawls.
* **Pronoun Grounding**: Automatically resolves pronouns (*"what is their pricing?"*) to previously discussed entities.

### 5. 🔍 Multi-Source Intelligence & Deal Screening
* **Tavily AI Search**: Purpose-built search engine for autonomous AI agents.
* **You.com Web & News API**: High-precision web snippets and live news search with automatic DuckDuckGo fallback.
* **TrustMRR SaaS Intelligence**: Live verified SaaS metrics (MRR, ARR, active subscribers, valuation multiples, and M&A deal screening).
* **Tech Stack & Infra Recon**: Scans SSL/CT logs, frameworks, package ecosystems (npm/PyPI), and DNS health.

### 6. 📄 Multimodal & Document Intelligence
* **Image Perception**: Full-fidelity vision support for screenshots, diagrams, and visual scenes.
* **Document Parsing**: Native text, section, and table extraction for `.pdf`, `.docx`, and `.csv` files.

### 7. 🎵 Media & Video Extraction
* **YouTube Downloader**: High-speed extraction of YouTube videos (MP4) and audio songs (MP3 at 192kbps) directly into your `~/Downloads` folder via `yt-dlp`.

### 8. ⚡ Multi-Agent JIT Swarm Conductor
* **Just-In-Time Specialist Delegation**: Automatically spawns domain-specific micro-agents in parallel for complex multi-faceted research missions.

---

## 🚀 Quick Start

### 1. Prerequisites
* **Python**: 3.10 or newer (3.12 recommended). Python 3.9 is no longer supported because it stopped getting security updates.
* **A Mac with Apple silicon** for on-device images, video, voice and training (MLX).
* **Google Chrome**: For the autonomous browser sandbox.
* **LLM Engine**: [LM Studio](https://lmstudio.ai), [Ollama](https://ollama.com), or an API key for any supported cloud provider.

### 2. Clone and Setup
```bash
# Clone the repository
git clone https://github.com/your-username/xod.git
cd xod

# Create and activate a virtual environment
python3.12 -m venv .venv   # any Python 3.10+
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment
```bash
# Copy the example environment template
cp .env.example .env
```
Open `.env` in your editor. If you are using **LM Studio** locally, the default configuration already works out of the box!

### 4. Launch Xod

**As a desktop app (Electron):**
```bash
cd electron
npm install
npm start
```
It starts Xod's server on a free local port and stops it when you quit. If `.venv` is missing it's created first (a few GB of packages, one time).

**Build the installer** (`electron/dist/Xod-<version>-arm64.dmg`):
```bash
cd electron && npm run dist
```
The installed app keeps its Python environment in the data folder and installs packages on first launch. It isn't signed yet, so macOS will say it can't verify the developer: right-click Xod.app → **Open**, or allow it in System Settings → Privacy & Security.

**In your browser:**
```bash
./run.sh
```
Then open 👉 **[http://localhost:8000](http://localhost:8000)**

### Where your data lives
Everything personal is kept outside the code, in a folder only your user account can read:
`~/Library/Application Support/Xod` (chats and memories in `xod.db`, projects, downloaded models, generated media, the agent's browser profile). Set `XOD_DATA_DIR` to put it somewhere else. API keys and saved site logins are in the macOS Keychain instead.

### Voice input
The mic button (or Option+Space) records until you pause, then transcribes **on your Mac** with Whisper (MLX). Audio is never uploaded. The speech model (~150 MB) downloads on first use; pick another with `XOD_WHISPER_MODEL`.

---

## 🤖 Model Providers & Local Inference

Xod supports seamless hot-swapping between model providers via `.env` or directly through the in-app **Settings ⚙️** modal:

| Provider | Type | Default Model / Target |
| :--- | :--- | :--- |
| **LM Studio** | Local | `google/gemma-4-12b-qat` (Runs on `http://localhost:1234/v1`) |
| **Ollama** | Local | `llama3.2`, `mistral`, `qwen2.5-coder` (`http://localhost:11434/v1`) |
| **Fireworks AI** | Cloud | `accounts/fireworks/models/deepseek-v3` |
| **OpenAI** | Cloud | `gpt-4o`, `gpt-4o-mini`, `o3-mini` |
| **Anthropic** | Cloud | `claude-3-5-sonnet-latest`, `claude-3-5-haiku-latest` |
| **Google Gemini** | Cloud | `gemini-2.5-flash`, `gemini-2.5-pro` |
| **Groq** | Cloud | `llama-3.3-70b-versatile` |
| **OpenRouter** | Cloud | Any model on OpenRouter |

---

## 📁 Repository Structure

```
xod/
├── app/
│   ├── main.py                  # FastAPI server, endpoints & SSE event stream
│   ├── config.py                # Environment & dynamic SQLite settings sync
│   ├── paths.py                 # Where personal data lives (outside the repo)
│   ├── agent/
│   │   ├── prompts.py           # Specialist persona system prompts
│   │   ├── tools.py             # 40+ function tool definitions & execution router
│   │   ├── researcher.py        # Autonomous ReAct agent loop & deliberation engine
│   │   ├── evidence.py          # EvidenceBoard observation aggregator
│   │   └── swarm.py             # JIT multi-agent specialist swarm conductor
│   ├── services/
│   │   ├── browser_sandbox.py   # Chrome CDP browser sandbox & Set-of-Marks engine
│   │   ├── system_control.py    # Host computer terminal, specs & background jobs
│   │   ├── filesystem.py        # Safe file & folder manipulation engine
│   │   ├── llm.py               # Unified local & cloud LLM streaming client
│   │   ├── storage.py           # Persistent SQLite database operations
│   │   ├── tavily_search.py     # Tavily AI Search client
│   │   ├── you_search.py        # You.com YDC Index Search client
│   │   ├── trustmrr.py          # TrustMRR SaaS verified financials & deals
│   │   ├── media_downloader.py  # yt-dlp video & audio converter
│   │   └── document_parser.py   # PDF, DOCX, and CSV structure parser
│   └── static/
│       ├── index.html           # Dark-mode dashboard UI & cockpit inspector
│       ├── css/style.css        # Responsive styling & status badge animations
│       └── js/app.js            # Reactive SSE streaming client & modals
├── electron/                    # Desktop app (Electron) and installer build
├── scripts/check_release.py     # Blocks keys, personal files and home paths from commits
├── .github/                     # CI: secret scan, dependency audit, desktop checks
├── run.sh                       # One-click startup script
├── requirements.txt             # Python dependencies
├── .env.example                 # Clean environment variable template
├── .gitignore                   # Comprehensive git protection rules
└── LICENSE                      # MIT License
```

---

## 🔒 Security & Privacy Notice

* **Local only**: the server listens on `127.0.0.1` and refuses requests from other sites (Host and Origin checks), so a web page you visit can't drive Xod.
* **Untrusted text stays text**: everything the AI writes can contain words from web pages, so replies are sanitized (DOMPurify) and the page runs under a strict Content-Security-Policy: no outside scripts, no requests anywhere but Xod itself. All UI libraries and fonts are bundled at pinned versions; nothing loads from CDNs.
* **Keys in the Keychain**: API keys and saved site logins live in the macOS Keychain, never in files, logs or prompts. Keys put in a `.env` file are moved into the Keychain on the next start.
* **Your data stays on your Mac**: everything personal is in `~/Library/Application Support/Xod`, outside the code. Xod has no telemetry, and voice is transcribed locally.
* **It asks first**: anything that submits, sends, pays, posts or deletes on a website waits for your approval, one action at a time.
* **Self-editing is gated**: Xod can only change its own code with a strong enough model, after tests pass, never the safety code, and never inside the installed app. Every change can be reverted.
* **Computer access is real**: Xod can run commands and edit files on your Mac when you ask it to. Review what it proposes.
* **Dependencies are audited**: exact versions in `requirements.txt` with no known vulnerabilities when pinned; CI re-audits weekly and Dependabot proposes updates.

See [SECURITY.md](SECURITY.md) to report a vulnerability.

---

## 🤝 Contributing

Contributions, feedback, and issue reports are warmly welcomed! Please check out [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

---

## 📄 License

This project is open-sourced under the [MIT License](LICENSE).
