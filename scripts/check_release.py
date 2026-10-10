#!/usr/bin/env python3
"""
Refuses to let private things into git.

  python3 scripts/check_release.py            # every file git would publish
  python3 scripts/check_release.py --staged   # only what's staged (the pre-commit hook uses this)

Fails on: personal data files (databases, .env, browser profiles, key files), anything that
looks like an API key or private key, your own home folder path or username, and files
over 5 MB. Uses only the standard library so it runs before any setup.
"""
import getpass
import os
import re
import subprocess
import sys

MAX_BYTES = 5 * 1024 * 1024

FORBIDDEN_PATHS = [
    (re.compile(r"(^|/)data/"), "the data folder (chats, memories, models)"),
    (re.compile(r"(^|/)\.env(\.|$)(?!example)"), "an .env file"),
    (re.compile(r"\.(db|sqlite3?|db-wal|db-shm)$"), "a database"),
    (re.compile(r"(^|/)xod_ai_browser_sandbox/"), "the agent's browser profile (cookies, logins)"),
    (re.compile(r"\.(pem|key|p12|pfx|keychain-db)$"), "a key or certificate"),
    (re.compile(r"(^|/)(vault_index|profile)\.json$"), "personal Xod settings"),
    (re.compile(r"(^|/)\.venv[^/]*/|(^|/)node_modules/"), "an installed environment"),
    (re.compile(r"(^|/)(dist|out)/.*\.(dmg|zip|app)"), "a build output"),
]

SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"), "Anthropic key"),
    (re.compile(r"sk-(proj-)?[A-Za-z0-9_-]{32,}"), "OpenAI-style key"),
    (re.compile(r"hf_[A-Za-z0-9]{30,}"), "Hugging Face token"),
    (re.compile(r"tvly-[A-Za-z0-9_-]{20,}"), "Tavily key"),
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "Google API key"),
    (re.compile(r"gsk_[A-Za-z0-9]{40,}"), "Groq key"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), "GitHub token"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"tmrr_[A-Za-z0-9]{16,}"), "TrustMRR key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
]

# Example values in forms and docs: tmrr_xxxxxxxx, sk-...your-key..., AAAAAAAA…
PLACEHOLDER = re.compile(r"(x{6,}|X{6,}|(.)\2{9,}|your|example|placeholder|dummy|test)", re.I)

SKIP_CONTENT = re.compile(r"\.(png|jpe?g|gif|ico|icns|woff2?|ttf|otf|webp|mp4|mp3|wav|pdf|lock)$|package-lock\.json$")


def files(staged: bool):
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"] if staged else \
          ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f.strip()]


def main() -> int:
    staged = "--staged" in sys.argv
    home = os.path.expanduser("~")
    user = getpass.getuser()
    personal = [p for p in {home, f"/Users/{user}/", f"/home/{user}/"} if len(p) > 8]
    problems = []
    this_file = os.path.relpath(__file__)
    for f in files(staged):
        if not os.path.isfile(f):
            continue
        for rx, what in FORBIDDEN_PATHS:
            if rx.search(f):
                problems.append(f"{f}: {what} must not be published")
        size = os.path.getsize(f)
        if size > MAX_BYTES:
            problems.append(f"{f}: {size // 1024 // 1024} MB is too big for the repo")
            continue
        if SKIP_CONTENT.search(f) or f == this_file:
            continue
        try:
            text = open(f, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for rx, what in SECRET_PATTERNS:
            for m in rx.finditer(text):
                if PLACEHOLDER.search(m.group(0)):
                    continue
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{f}:{line}: looks like a {what}")
                break
        hit = next((p for p in personal if p in text), None)
        if hit:
            line = text.count("\n", 0, text.index(hit)) + 1
            problems.append(f"{f}:{line}: contains your home folder path")
    if problems:
        print("✋ Not safe to publish yet:\n  " + "\n  ".join(problems))
        print("\nFix these (or add the file to .gitignore). Secrets belong in the Keychain, not in files.")
        return 1
    print(f"✓ {'Staged files' if staged else 'Files to publish'} look clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
