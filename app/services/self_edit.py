"""
Self-editing: Xod changing its own code, behind a gate that depends on which model writes the code.

Tiers (by provider + model name; see DEFAULT_RULES, overridable in data/self_edit/settings.json):
  green   strong coding models (Claude Opus / Fable, top OpenAI models, a few known-strong local
          coders): changes are staged, checked, and applied automatically when Xod is idle.
  yellow  capable general models (e.g. Gemma 12B in LM Studio): same staging and checks, but the
          change waits as a proposal until you approve it.
  red     small models (mini-model students, anything under ~7B): no code changes.
An "editor" model can be set separately: Gemma keeps chatting, Claude writes the code.

Every change, at every tier:
  1. Only files under app/ and tests/ can change; never data/, .env, or this gate itself.
  2. Protected code is off-limits: the approval gates for submit/pay/send, typed-value grounding,
     the passwords and API key stores, the side-effect list that stops repairs re-running actions,
     and this module. An edit that changes any of it is rejected before it's staged.
  3. Staged in a copy of the app, then checked there: Python/JS syntax, the app imports, and the
     fast test suites (run from the ORIGINAL test files, so a change can't edit the tests to pass).
  4. Applied with a backup of every file it touches; one click (or `python -m app.services.self_edit
     revert <id>`, which works even if the server won't start) puts the old code back.
  5. Applied only when no chat reply or task is running, because the dev server reloads on change.
"""
import ast
import asyncio
import difflib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from app.paths import DATA_DIR

logger = logging.getLogger("xod.self_edit")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
WORK_DIR = DATA_DIR / "self_edit"
SETTINGS_PATH = WORK_DIR / "settings.json"
HISTORY_PATH = WORK_DIR / "history.json"
PYTHON = Path(sys.executable)   # same Python as the server

MAX_FILES = 6
MAX_CHANGED_LINES = 300
CHECK_TIMEOUT_SEC = 240

EDITABLE_ROOTS = ("app/", "tests/")
PROTECTED_FILES = {
    "app/services/self_edit.py",          # the gate can't loosen itself
    "app/services/secret_store.py",       # API keys
    "app/services/credential_vault.py",   # saved logins
    "app/config.py",                      # where keys are loaded
    "app/paths.py",                       # where personal data lives
    "app/services/mcp_client.py",         # approval gate for outside tools, their Keychain secrets
    "app/services/activity.py",           # the undo log
    "app/services/llm_routing.py",        # when Xod may spend money on cloud models (budget)
    "app/static/js/safe-html.js",         # sanitizer for everything the AI writes
    "app/services/media_packages.py",     # installs packages from PyPI (what, and which versions)
    "app/services/mail_crypto.py",        # Agent Mail encryption and signatures
    "app/services/agent_mail.py",         # Agent Mail trust rules: key pinning, drafts need approval
    "app/services/speech.py",             # runs the voice: text must never reach a shell
    "app/services/mcp_oauth.py",          # sign-in tokens for connected apps
    "app/services/connectors.py",         # which outside servers Xod connects to
}
# file -> top-level names whose source must stay byte-for-byte the same
PROTECTED_SYMBOLS = {
    "app/services/browser_sandbox.py": {"_looks_like_final_submit", "_value_is_grounded", "_OUTWARD_WORDS", "_ACCOUNT_WORDS"},
    "app/agent/healing.py": {"SIDE_EFFECT_TOOLS", "MAX_REPAIRS_PER_CALL"},
    "app/services/media_worker.py": {"_memory_watchdog", "MEMORY_FLOOR_GB"},
    "app/main.py": {"local_only_guard", "_host_only", "_LOCAL_HOSTS", "_SECURITY_HEADERS"},   # localhost-only + CSP
    "app/services/reports.py": {"clean", "build_html", "_BAD_BLOCKS", "_EVENT_ATTR", "_JS_URL"},   # reports can't run code
}
# Text an edit may not add or remove anywhere: the approval flow for irreversible clicks.
PROTECTED_TEXT = ("pending_submit", "allow_submit", "done_gated", "_mission_lock",
                  "packs.tool_allowed(", "mcp_hub.call(")   # add-on gate and MCP routing in the tool dispatcher

# (pattern on "provider:model", tier, why). First match wins.
DEFAULT_RULES: List[Tuple[str, str, str]] = [
    (r"^specialist:|qwen2\.5-1\.5b|-0\.5b|-1\.5b|-3b\b|-1b\b", "red", "small model: too error-prone to change code"),
    (r"^anthropic:claude-(opus|fable)", "green", "frontier coding model"),
    (r"^openrouter:anthropic/claude-(opus|fable)", "green", "frontier coding model"),
    (r"^(openai|openrouter):(openai/)?(gpt-5|o3|o4)|astra", "green", "frontier coding model"),
    (r"qwen3-coder-(30b|480b)|devstral|gpt-oss-120b|glm-4\.[56]|glm-5|kimi-k2|deepseek-(v3|r1)", "green", "known strong coding model"),
    (r"^anthropic:claude-sonnet", "yellow", "capable, but changes wait for your approval"),
]
YELLOW_MIN_B = 7.0   # local models at least this many billion parameters can propose changes


# ---------------------------------------------------------------------- settings & tiers
def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def get_settings() -> Dict[str, Any]:
    s = {"enabled": True, "auto_apply_green": True, "editor": None, "tier_overrides": {}}
    s.update(_read_json(SETTINGS_PATH, {}))
    return s


def save_settings(update: Dict[str, Any]) -> Dict[str, Any]:
    s = get_settings()
    for k in ("enabled", "auto_apply_green", "editor", "tier_overrides"):
        if k in update:
            s[k] = update[k]
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, indent=1))
    return s


def tier_for(provider: str, model: str) -> Dict[str, str]:
    key = f"{provider}:{model}".lower()
    override = get_settings()["tier_overrides"].get(key)
    if override in ("green", "yellow", "red"):
        return {"tier": override, "why": "set by you"}
    for pattern, tier, why in DEFAULT_RULES:
        if re.search(pattern, key):
            return {"tier": tier, "why": why}
    m = re.search(r"(\d+(?:\.\d+)?)\s*b\b", key.split(":", 1)[-1].replace("_", "-"))
    if m:
        size = float(m.group(1))
        return ({"tier": "yellow", "why": f"{size:g}B local model: can propose changes for your approval"} if size >= YELLOW_MIN_B
                else {"tier": "red", "why": f"{size:g}B model: too small to change code"})
    if provider in ("lm_studio", "ollama", "xod"):
        return {"tier": "red", "why": "unknown local model size"}
    return {"tier": "yellow", "why": "unrecognised API model: can propose changes for your approval"}


def editor_model() -> Dict[str, Any]:
    """Which model would write code right now, and its tier."""
    from app.config import settings
    ed = get_settings().get("editor") or {}
    if ed.get("provider") and ed.get("model"):
        key_field = f"{ed['provider']}_api_key"
        if ed["provider"] in ("lm_studio", "ollama", "xod") or getattr(settings, key_field, ""):
            return {"provider": ed["provider"], "model": ed["model"], "source": "editor setting",
                    **tier_for(ed["provider"], ed["model"])}
        missing = f"the {ed['provider']} API key isn't set"
    else:
        missing = None
    out = {"provider": settings.llm_provider, "model": settings.llm_model, "source": "chat model",
           **tier_for(settings.llm_provider, settings.llm_model)}
    if missing:
        out["note"] = f"Editor model {ed['model']} isn't usable ({missing}); using the chat model."
    return out


# ---------------------------------------------------------------------- history
def _history() -> List[Dict[str, Any]]:
    return _read_json(HISTORY_PATH, [])


def _save_history(h: List[Dict[str, Any]]) -> None:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(h[-100:], indent=1, default=str))


def _update_change(change_id: str, **fields) -> Dict[str, Any]:
    h = _history()
    for c in h:
        if c["id"] == change_id:
            c.update(fields, updated_at=time.time())
            _save_history(h)
            return c
    raise KeyError(change_id)


def get_change(change_id: str) -> Optional[Dict[str, Any]]:
    return next((c for c in _history() if c["id"] == change_id), None)


def list_changes(limit: int = 30) -> List[Dict[str, Any]]:
    return list(reversed(_history()))[:limit]


# ---------------------------------------------------------------------- validating edits
def _rel(path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(BASE_DIR)
        except ValueError:
            return "../outside"
    return str(Path(os.path.normpath(str(p)))).replace("\\", "/")


def _symbol_sources(src: str, names: set) -> Dict[str, str]:
    out = {}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {n: "<unparseable>" for n in names}
    for node in tree.body:
        targets = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            targets = [node.name]
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        for t in targets:
            if t in names:
                # Decorators count: removing @app.middleware(...) would switch a guard off.
                decos = "".join("@" + (ast.get_source_segment(src, d) or "") + "\n" for d in getattr(node, "decorator_list", []))
                out[t] = decos + (ast.get_source_segment(src, node) or "")
    return out


def check_edit_policy(rel: str, before: Optional[str], after: str) -> Optional[str]:
    """Why this file change isn't allowed, or None."""
    if rel.startswith("../") or not rel.startswith(EDITABLE_ROOTS):
        return f"{rel}: only files under app/ and tests/ can be changed"
    if rel in PROTECTED_FILES:
        return f"{rel} is protected safety code (keys, logins, approvals, undo, budget, package installs or the self-edit gate)"
    if "/__pycache__/" in rel or rel.endswith((".pyc", ".db", ".env")):
        return f"{rel}: not a source file"
    if rel in PROTECTED_SYMBOLS and before is not None:
        names = PROTECTED_SYMBOLS[rel]
        a, b = _symbol_sources(before, names), _symbol_sources(after, names)
        changed = [n for n in names if a.get(n) != b.get(n)]
        if changed:
            return f"{rel}: protected safety code can't change ({', '.join(sorted(changed))})"
    for t in PROTECTED_TEXT:
        if (before or "").count(t) != after.count(t):
            return f"{rel}: changes to the approval flow ({t}) aren't allowed"
    return None


def apply_edits_to_text(original: Optional[str], edits: List[Dict[str, Any]], rel: str) -> Tuple[Optional[str], Optional[str]]:
    """Returns (new_text, error)."""
    text = original
    for e in edits:
        if "create" in e:
            if original is not None:
                return None, f"{rel} already exists; use find/replace to change it"
            text = e["create"]
            continue
        if text is None:
            return None, f"{rel} doesn't exist"
        find, repl = e.get("find", ""), e.get("replace", "")
        if not find:
            return None, f"{rel}: an edit is missing its 'find' text"
        n = text.count(find)
        if n == 0:
            return None, f"{rel}: couldn't find the text to replace (it must match the file exactly): {find[:120]!r}"
        if n > 1:
            return None, f"{rel}: the text to replace appears {n} times; include more surrounding lines so it's unique"
        text = text.replace(find, repl, 1)
    return text, None


def build_change(edits: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Validates a list of {path, find, replace} / {path, create} into per-file before/after, or an error."""
    by_file: Dict[str, List[Dict[str, Any]]] = {}
    for e in edits or []:
        if not isinstance(e, dict) or not e.get("path"):
            return {"error": "every edit needs a path"}
        by_file.setdefault(_rel(e["path"]), []).append(e)
    if not by_file:
        return {"error": "no edits"}
    if len(by_file) > MAX_FILES:
        return {"error": f"too many files ({len(by_file)}; max {MAX_FILES}); split the change"}
    files, changed_lines, diff = {}, 0, []
    for rel in by_file:  # path rules first: never even read a file outside what may change
        if rel.startswith("../") or not rel.startswith(EDITABLE_ROOTS) or rel in PROTECTED_FILES:
            return {"error": check_edit_policy(rel, None, ""), "blocked": True}
    for rel, es in by_file.items():
        p = BASE_DIR / rel
        before = p.read_text() if p.exists() else None
        after, err = apply_edits_to_text(before, es, rel)
        if err:
            return {"error": err}
        why = check_edit_policy(rel, before, after)
        if why:
            return {"error": why, "blocked": True}
        d = list(difflib.unified_diff((before or "").splitlines(), after.splitlines(), f"a/{rel}", f"b/{rel}", lineterm="", n=2))
        changed_lines += sum(1 for l in d if l[:1] in "+-" and not l.startswith(("+++", "---")))
        diff += d
        files[rel] = {"before": before, "after": after}
    if changed_lines > MAX_CHANGED_LINES:
        return {"error": f"change too big ({changed_lines} lines; max {MAX_CHANGED_LINES}); split it"}
    return {"files": files, "diff": "\n".join(diff), "changed_lines": changed_lines}


# ---------------------------------------------------------------------- staging & checks
def _copy_tree(dst: Path) -> None:
    for root in ("app", "tests"):
        shutil.copytree(BASE_DIR / root, dst / root,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))


def run_checks(change: Dict[str, Any], change_id: str) -> Dict[str, Any]:
    """Stages the change in a copy of the app and runs syntax, import and fast test checks there."""
    # Outside the project: the dev server reloads on any .py change under it, which would cut off replies.
    import tempfile
    stage = Path(tempfile.mkdtemp(prefix=f"xod_{change_id}_"))
    _copy_tree(stage)
    for rel, f in change["files"].items():
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f["after"])
    # Tests always run from the original files: a change can't make checks pass by editing them.
    shutil.rmtree(stage / "tests", ignore_errors=True)
    shutil.copytree(BASE_DIR / "tests", stage / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    env = dict(os.environ, PYTHONPATH=str(stage), XOD_DATASET_DB=str(stage / "check_dataset.db"),
               XOD_DATA_DIR=str(stage / "_check_data"), PYTHONDONTWRITEBYTECODE="1")  # never the real data
    results = []

    def run(name, cmd, timeout=60):
        try:
            p = subprocess.run(cmd, cwd=str(stage), env=env, capture_output=True, text=True, timeout=timeout)
            ok = p.returncode == 0 and "SUMMARY FAIL" not in p.stdout
            out = (p.stdout + p.stderr)[-1500:]
        except subprocess.TimeoutExpired:
            ok, out = False, f"timed out after {timeout}s"
        results.append({"check": name, "ok": ok, "output": out if not ok else out[-300:]})
        return ok

    for rel in change["files"]:
        if rel.endswith(".py"):
            run(f"syntax {rel}", [str(PYTHON), "-m", "py_compile", rel], 30)
        elif rel.endswith(".js"):
            run(f"syntax {rel}", ["node", "--check", rel], 30)
    if all(r["ok"] for r in results):
        run("Xod starts (imports cleanly)", [str(PYTHON), "-c", "import app.main"], 90)
    if all(r["ok"] for r in results):
        for t in ("tests/self_edit/smoke_test.py", "tests/self_heal/run_learning_jobs_test.py"):
            if (stage / t).exists():
                run(f"tests: {t}", [str(PYTHON), "-u", t], CHECK_TIMEOUT_SEC)
    shutil.rmtree(stage, ignore_errors=True)
    return {"ok": all(r["ok"] for r in results), "results": results}


# ---------------------------------------------------------------------- applying & reverting
def _apply_files(change_id: str, files: Dict[str, Dict[str, Optional[str]]]) -> None:
    backup = WORK_DIR / "changes" / change_id
    backup.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for rel, f in files.items():
        p = BASE_DIR / rel
        current = p.read_text() if p.exists() else None
        if current != f["before"]:
            raise RuntimeError(f"{rel} changed since this edit was checked; ask again so it's re-checked")
        manifest[rel] = current is not None
        if current is not None:  # .bak: a .py under the project would trigger a server reload
            (backup / (rel + ".bak")).parent.mkdir(parents=True, exist_ok=True)
            (backup / (rel + ".bak")).write_text(current)
    (backup / "manifest.json").write_text(json.dumps(manifest))
    for rel, f in files.items():
        p = BASE_DIR / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f["after"])


def revert(change_id: str) -> Dict[str, Any]:
    c = get_change(change_id)
    if not c or c.get("status") != "applied":
        return {"ok": False, "detail": "Only applied changes can be reverted."}
    backup = WORK_DIR / "changes" / change_id
    manifest = _read_json(backup / "manifest.json", None)
    if manifest is None:
        return {"ok": False, "detail": "The backup for this change is missing."}
    for rel, existed in manifest.items():
        p = BASE_DIR / rel
        if existed:
            p.write_text((backup / (rel + ".bak")).read_text())
        elif p.exists():
            p.unlink()
    _update_change(change_id, status="reverted", reverted_at=time.time())
    return {"ok": True, "detail": f"Put back the code from before “{c['summary']}”."}


_pending_apply: List[str] = []
active_streams = 0   # incremented by the chat endpoint while a reply is streaming


def _busy() -> bool:
    try:
        from app.services.task_queue import task_queue
        return active_streams > 0 or bool(task_queue._running_id)
    except Exception:
        return active_streams > 0


async def _apply_when_idle(change_id: str) -> None:
    """The dev server reloads when code changes, which would cut off a reply or task mid-way: wait for quiet."""
    for _ in range(600):
        if not _busy():
            await asyncio.sleep(3)
            if not _busy():
                break
        await asyncio.sleep(2)
    c = get_change(change_id)
    if not c or c["status"] != "approved":
        return
    try:
        _apply_files(change_id, {rel: {"before": f["before"], "after": f["after"]} for rel, f in c["files"].items()})
        _update_change(change_id, status="applied", applied_at=time.time())
        logger.info(f"Self-edit {change_id} applied: {c['summary']}")
    except Exception as e:
        _update_change(change_id, status="failed", error=str(e))


def approve(change_id: str) -> Dict[str, Any]:
    c = get_change(change_id)
    if not c or c["status"] != "proposed":
        return {"ok": False, "detail": "Only proposed changes can be approved."}
    _update_change(change_id, status="approved", approved_at=time.time(), approved_by="you")
    asyncio.get_event_loop().create_task(_apply_when_idle(change_id))
    return {"ok": True, "detail": "Approved. It applies as soon as Xod is idle (the app reloads itself)."}


def reject(change_id: str) -> Dict[str, Any]:
    c = get_change(change_id)
    if not c or c["status"] != "proposed":
        return {"ok": False, "detail": "Only proposed changes can be rejected."}
    _update_change(change_id, status="rejected")
    return {"ok": True, "detail": "Rejected; nothing changed."}


# ---------------------------------------------------------------------- writing the change
EDITOR_PROMPT = """You are changing the source code of Xod (a FastAPI app; Python backend in app/, browser UI in app/static/).
Make the smallest correct change that achieves the goal. Match the surrounding code's style.

Return JSON only:
{"summary": "<one line: what this changes, for the user>",
 "edits": [
   {"path": "app/...", "find": "<exact existing text, enough lines to be unique>", "replace": "<new text>"},
   {"path": "app/... or tests/...", "create": "<full content of a NEW file>"}
 ]}

Rules:
- "find" must match the file EXACTLY (spaces, indentation) and appear once.
- Only files under app/ or tests/. At most 6 files, 300 changed lines.
- Never change: the approval gates for submitting/paying/sending (pending_submit, allow_submit, _looks_like_final_submit,
  _value_is_grounded), API key or password storage, SIDE_EFFECT_TOOLS, or the self-edit gate. Edits touching them are rejected.
- If the goal can't be done safely within these rules, return {"summary": "can't do this: <why>", "edits": []}."""

_STOP = set("""the a an to of and or for in on with is it its this that be when what how xod make add fix change should
does doesnt don't can your yours own code instead default defaults seconds second minutes tool tools so from into by
please want need just more less than then also new use using used work works""".split())


def _gather_context(goal: str, hint_files: List[str], budget_chars: int) -> str:
    """Finds the code the goal is about. Rare, specific words (identifiers like computer_wait_for,
    numbers like 120) count far more than common ones, so the big UI file doesn't win on "tool"."""
    import math
    tokens = {t for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}|\b\d{2,}\b", goal) if t.lower() not in _STOP}
    files = {}
    for p in (BASE_DIR / "app").rglob("*"):
        if p.suffix not in (".py", ".js", ".html", ".css") or "vendor" in p.parts or "__pycache__" in p.parts:
            continue
        try:
            files[str(p.relative_to(BASE_DIR))] = p.read_text(errors="replace")
        except Exception:
            continue
    n = len(files) or 1
    df = {t: sum(1 for text in files.values() if t in text) for t in tokens}
    weight = {t: (math.log((n + 1) / (df[t] + 1)) + 0.05) * (3 if "_" in t or any(c.isupper() for c in t[1:]) else 1)
              for t in tokens if df[t]}
    # A tool named in the goal: follow it to the code that implements it (its dispatch branch in
    # tools.py calls into a service module, which usually holds the behaviour to change).
    followed = set()
    tools_src = files.get("app/agent/tools.py", "")
    for t in tokens:
        m = re.search(rf'elif name == "{re.escape(t)}":(.*?)(?=\n        elif name ==|\n    except)', tools_src, re.S)
        if m:
            for mod in re.findall(r"from app\.(services|agent)\.(\w+) import", m.group(1)):
                followed.add(f"app/{mod[0]}/{mod[1]}.py")
            for svc in re.findall(r"(\w+_service)\.", m.group(1)):
                hit = next((r for r, txt in files.items() if r.startswith("app/services/") and f"{svc} = " in txt), None)
                if hit:
                    followed.add(hit)
    scored = []
    for rel, text in files.items():
        sc = sum(min(text.count(t), 5) * w for t, w in weight.items())
        sc /= 1 + math.log(1 + len(text) / 20_000)   # big files match everything a little
        if rel in hint_files:
            sc += 1000
        if rel in followed:
            sc += 500
        if sc > 0:
            scored.append((sc, rel, text))
    scored.sort(key=lambda x: -x[0])
    parts, used = [], 0
    for _, rel, text in scored[:4]:
        room = min(budget_chars - used, max(4000, budget_chars // 2))   # leave room for the other files
        if room < 1500:
            break
        if len(text) <= min(14_000, room):
            chunk = f"### {rel} (whole file)\n{text}"
        else:
            lines = text.splitlines()
            ranked = sorted(((sum(w for t, w in weight.items() if t in l), i) for i, l in enumerate(lines)), reverse=True)
            hits = sorted(i for s_, i in ranked[:8] if s_ > 0)
            spans = []
            for i in hits:
                a, b = max(0, i - 30), min(len(lines), i + 30)
                if spans and a <= spans[-1][1]:
                    spans[-1] = (spans[-1][0], b)
                else:
                    spans.append((a, b))
            chunk = "\n\n".join(f"### {rel} (lines {a + 1}-{b})\n" + "\n".join(lines[a:b]) for a, b in spans)
        chunk = chunk[:room]
        parts.append(chunk)
        used += len(chunk)
    return "\n\n".join(parts)


async def _ask_editor(ed: Dict[str, Any], messages: List[Dict[str, Any]]) -> Dict[str, Any]:
    from app.config import settings
    from app.services.llm import llm_service, parse_llm_json
    same_as_chat = ed["provider"] == settings.llm_provider and ed["model"] == settings.llm_model
    override = None if same_as_chat else llm_service._build_endpoint(ed["provider"], ed["model"])
    # Local models think at ~15-20 tokens/s here: a long think blew past the 5-minute timeout three times
    # in a row. They answer directly (the staged checks still catch mistakes, with one retry);
    # API models think.
    local = ed["provider"] in ("lm_studio", "ollama", "xod")
    reply = await llm_service.chat_completion(messages=messages, temperature=0.1, max_tokens=3500 if local else 8000,
                                              endpoint_override=override, think=not local, task="self_edit")
    return parse_llm_json(reply.get("content", "")) or {}


async def request_change(goal: str, hint_files: Optional[List[str]] = None, requested_by: str = "agent") -> Dict[str, Any]:
    """Writes, checks, and (by tier) applies or proposes a change to Xod's own code."""
    s = get_settings()
    if not s["enabled"]:
        return {"success": False, "error": "Self-editing is turned off (Tools → Health → Self-editing)."}
    if os.environ.get("XOD_PACKAGED"):
        # Code inside Xod.app is signed and read-only; changing it would break the app.
        return {"success": False, "error": "Self-editing only works when Xod runs from its source code (git clone), not from the installed app."}
    ed = editor_model()
    if ed["tier"] == "red":
        return {"success": False, "tier": "red",
                "error": f"{ed['model']} isn't allowed to change Xod's code ({ed['why']}). Set a stronger editor model "
                         "in Tools → Health → Self-editing, e.g. Claude Opus with an Anthropic key."}
    change_id = f"edit-{uuid.uuid4().hex[:8]}"
    local = ed["provider"] in ("lm_studio", "ollama", "xod")
    # Local models are slow to read long prompts: a tighter excerpt keeps them under the timeout.
    context = _gather_context(goal, [_rel(f) for f in hint_files or []], 22_000 if local else 150_000)
    messages = [{"role": "system", "content": EDITOR_PROMPT},
                {"role": "user", "content": f"GOAL: {goal}\n\nRELEVANT CODE:\n{context}"}]
    attempts = []
    change: Dict[str, Any] = {}
    plan: Dict[str, Any] = {}
    for attempt in (1, 2):
        try:
            plan = await _ask_editor(ed, messages)
        except Exception as e:
            return {"success": False, "error": f"The editor model ({ed['model']}) couldn't be reached: {e}"}
        if not plan.get("edits"):
            return {"success": False, "error": plan.get("summary") or "The editor model didn't propose any change."}
        change = build_change(plan["edits"])
        if "error" not in change:
            checks = await asyncio.get_event_loop().run_in_executor(None, run_checks, change, change_id)
            if checks["ok"]:
                break
            problem = "Checks failed:\n" + "\n".join(f"- {r['check']}: {r['output'][-600:]}" for r in checks["results"] if not r["ok"])
        else:
            if change.get("blocked"):
                attempts.append(change["error"])
                return {"success": False, "blocked": True, "error": f"Refused: {change['error']}"}
            problem = change["error"]
        attempts.append(problem[:1500])
        if attempt == 2:
            return {"success": False, "error": f"The change didn't pass: {problem[:800]}", "attempts": attempts}
        messages += [{"role": "assistant", "content": json.dumps(plan)[:20000]},
                     {"role": "user", "content": f"That didn't work. {problem}\nFix it and return the full JSON again."}]

    auto = ed["tier"] == "green" and s["auto_apply_green"]
    record = {"id": change_id, "created_at": time.time(), "goal": goal, "summary": plan.get("summary", goal)[:200],
              "model": f"{ed['provider']}:{ed['model']}", "tier": ed["tier"], "requested_by": requested_by,
              "status": "approved" if auto else "proposed", "files": change["files"], "diff": change["diff"],
              "changed_lines": change["changed_lines"], "checks": checks["results"], "attempts": attempts}
    h = _history()
    h.append(record)
    _save_history(h)
    if auto:
        asyncio.get_event_loop().create_task(_apply_when_idle(change_id))
    return {"success": True, "change_id": change_id, "status": record["status"], "tier": ed["tier"], "model": record["model"],
            "summary": record["summary"], "files": list(change["files"]), "changed_lines": change["changed_lines"],
            "checks_passed": [r["check"] for r in checks["results"]],
            "next": ("It applies automatically as soon as Xod is idle (the app reloads itself). Revert any time in Tools → Health."
                     if auto else "It's waiting for your approval in Tools → Health → Self-editing.")}


def public_change(c: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in c.items() if k != "files"} | {"files": list(c.get("files", {}))}


def status() -> Dict[str, Any]:
    from app.config import settings
    return {"settings": get_settings(), "editor": editor_model(),
            "chat_model": {"provider": settings.llm_provider, "model": settings.llm_model,
                           **tier_for(settings.llm_provider, settings.llm_model)},
            "protected": sorted(PROTECTED_FILES) + [f"{f}: {', '.join(sorted(n))}" for f, n in PROTECTED_SYMBOLS.items()],
            "changes": [public_change(c) for c in list_changes()]}


if __name__ == "__main__":
    # Works even when the server won't start: python -m app.services.self_edit revert <id> | revert-last | list
    cmd = sys.argv[1:] or ["list"]
    if cmd[0] == "list":
        for c in list_changes():
            print(c["id"], c["status"], c["summary"])
    elif cmd[0] in ("revert", "revert-last"):
        cid = cmd[1] if cmd[0] == "revert" else next((c["id"] for c in list_changes() if c["status"] == "applied"), None)
        print(revert(cid) if cid else "Nothing applied to revert.")
