# Security Policy

Xod runs on your own computer and can browse the web, run commands, and read and write files for you, so security reports are taken seriously.

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Use GitHub's private reporting instead: the repository's **Security** tab → **Report a vulnerability**.

Include what you found, how to reproduce it, and what an attacker could do with it. You'll get a reply within a week.

## What's in scope

- Ways for a website, file, or document to make Xod act without the user's approval (prompt injection that bypasses the approval gates, cross-site requests to the local server).
- Ways to read API keys or saved logins from the Keychain, logs, prompts, or the UI.
- Ways for the self-editing feature to change protected safety code or skip its checks.
- The Electron shell loading or navigating to content other than Xod itself.

## Design notes

- The server binds to `127.0.0.1` and rejects requests whose `Host` isn't local or whose `Origin` isn't Xod's own address.
- AI output is sanitized with DOMPurify (`app/static/js/safe-html.js`) and the UI runs under a Content-Security-Policy (`app/main.py`). UI libraries are vendored in `app/static/vendor/` at pinned versions.
- Personal data lives outside the code in `~/Library/Application Support/Xod` (`app/paths.py`), readable only by the user.
- Secrets live only in the macOS Keychain (`app/services/secret_store.py`, `app/services/credential_vault.py`).
- Outward actions on websites (submit, send, pay, post, delete, create account) need one approval per action (`app/services/browser_sandbox.py`).
- Outside tools (MCP) run only after approval unless the user trusts that server; read-only detection uses the tool's own annotations (`app/services/mcp_client.py`). Their secrets go to the Keychain.
- Every tool call is logged, and file changes are snapshotted first so they can be undone (`app/services/activity.py`).
- Cloud models are used only if the user turns that on, within a monthly budget (`app/services/llm_routing.py`).
- Self-editing rules and the protected code list are in `app/services/self_edit.py`. Self-edits can't touch any of the safety code above.
