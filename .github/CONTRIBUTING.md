# Contributing to Xod

Thank you for your interest in contributing to **Xod**! We welcome contributions of all kinds: bug fixes, new tool integrations, prompt improvements, UI enhancements, and documentation updates.

---

## 🛠️ Development Setup

1. **Fork and clone the repository**:
   ```bash
   git clone https://github.com/CloudCorpRecords/xod.git
   cd xod
   ```

2. **Create a virtual environment**:
   ```bash
   python3.12 -m venv .venv   # any Python 3.10+
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configure your environment**:
   ```bash
   cp .env.example .env
   ```

4. **Run the development server** (reloads on code changes):
   ```bash
   ./run.sh
   ```
   Or the desktop app: `cd electron && npm install && npm start`.

5. **Run the tests** before opening a pull request:
   ```bash
   .venv/bin/python tests/smoke/run_smoke_test.py         # every endpoint and panel, security headers (needs Chrome)
   .venv/bin/python tests/smoke/run_companion_test.py     # the companion through a whole chat, every look (needs Chrome)
   .venv/bin/python tests/smoke/run_speech_test.py        # spoken replies, end to end (needs Chrome)
   .venv/bin/python tests/smoke/run_conversation_test.py  # "Hey Xod" conversations with a fake microphone (needs Chrome and a model)
   .venv/bin/python tests/smoke/run_local_models_test.py  # Ollama edge cases: context, crashes, missing models (--real: your Ollama too)
   .venv/bin/python tests/perf/run_speed_bench.py --warm  # how long answers take, call by call, on your model
   .venv/bin/python tests/features/run_features_test.py   # undo, routines, routing, add-ons, reports, MCP
   .venv/bin/python tests/features/run_connectors_test.py # connected apps and sign-in (needs internet)
   .venv/bin/python tests/features/run_agent_mail_test.py # Agent Mail: a relay and three Xods, encryption, approvals
   .venv/bin/python tests/self_heal/run_heal_test.py
   .venv/bin/python tests/self_heal/run_learning_jobs_test.py
   .venv/bin/python tests/self_edit/run_self_edit_test.py   # needs a local model in LM Studio
   ```

6. **Build the Mac installer** (optional). Needs Node and a standalone Python 3.12 (`uv python install 3.12`):
   ```bash
   cd electron && npm install && npm run dist   # → electron/dist/Xod-<version>-arm64.dmg
   ```
   The build installs every package into a Python that ships inside Xod.app, so people who download it install nothing on first launch. Media Studio's heavy libraries (PyTorch, diffusers) and ffmpeg are fetched on first use instead (`app/services/media_packages.py`). It also writes the notices for everything bundled (`scripts/third_party_licenses.py`) and fails on GPL-family licenses.

   Releases: write what changed under `## 0.5.1` (the new version) at the top of `docs/CHANGELOG.md`, commit it, then run `python scripts/release.py 0.5.1`. It sets the version everywhere, tags and pushes; `.github/workflows/release.yml` then builds the Mac and Windows apps and publishes the release, and installed copies update themselves within an hour. `--dry-run` shows what it would do.

7. **Turn on the release check** (once per clone). It blocks commits that contain keys, databases, `.env` files or your home folder path:
   ```bash
   printf '#!/bin/sh\nexec python3 scripts/check_release.py --staged\n' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
   ```

Your personal data lives outside the repo (`~/Library/Application Support/Xod`); tests use `test_runs/` inside it. Never put real API keys in code or tests.

---

## 📐 Project Guidelines

* **Code Style**: Keep Python code clean, typed, and formatted. Maintain documentation integrity and descriptive docstrings.
* **Tool Additions**:
  - Add tool function schemas in `app/agent/tools.py` under `TOOL_DEFINITIONS`.
  - Add execution dispatch handling in `execute_tool`.
  - Add tool affinity logic in `select_tools_for_query`.
  - Register friendly labels and icons in `app/static/js/app.js` (`formatToolName` and `getToolIcon`).
* **Security**:
  - Never commit API keys, personal directories, or credentials.
  - Respect system path protections in `app/services/filesystem.py`.

---

## 🚀 Submitting a Pull Request

1. Create a feature branch (`git checkout -b feature/amazing-feature`).
2. Test your changes locally to ensure the app compiles and launches cleanly.
3. Commit your changes (`git commit -m 'Add amazing feature'`).
4. Push to your branch (`git push origin feature/amazing-feature`).
5. Open a Pull Request describing your changes.

---

## 📄 License

By contributing to Xod, you agree that your contributions will be licensed under its [MIT License](../LICENSE).
