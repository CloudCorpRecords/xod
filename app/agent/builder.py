"""
Building apps and games: "make me a 3D game", "build a pomodoro timer", then "make the enemies faster".

The general planner decides every tool call up front, so it can't write a real program (it wrote an
84-byte stub and the summary described files that didn't exist). The builder works like a developer:

  1. plan   one small model call: a title, a folder name and 1-4 files (plain HTML/CSS/JS, no build step)
  2. write  each file in full, one model call per file, streamed so you see it grow
  3. run    open it in headless Chrome, press a few keys, collect errors and files that didn't load
  4. fix    give the model the errors and the code, write the fixed files, run again (twice at most)
  5. show   the preview pane opens on it; the answer lists only files that are really on disk

Changes to an app ("add a score", "fix the errors in the preview") rewrite only the files that
need it, then run and fix the same way. Every write goes through the normal tool path, so it's in
Activity with Undo, and the companion reacts to it.

Apps live in a folder of the project you're in. Outside a project, the builder makes a new
project for the app so it shows up on the left.
"""
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from app.services import previews
from app.services.llm import llm_service, parse_llm_json

logger = logging.getLogger("xod.builder")

ALLOWED_EXT = {".html", ".css", ".js", ".mjs", ".json", ".svg", ".txt", ".md", ".glsl"}
MAX_FILE_CHARS = 60000

_THINGS = (r"game|app|application|web ?site|web ?page|webpage|landing page|html page|site|tool|calculator|dashboard|timer|"
           r"stopwatch|clock|to-?do( list)?|quiz|simulation|simulator|visuali[sz]ation|animation|prototype|editor|(?:music|video|audio|media|mp3) player|tracker|"
           r"planner|portfolio|platformer|shooter|puzzle|snake|tetris|pong|breakout|chess|checkers|demo|widget|generator|"
           r"converter|drum machine|synth|piano|paint program|drawing app|kanban|flashcards?|playground|screensaver|3d scene|scene")
BUILD_RE = re.compile(rf"\b(make|build|create|code|write|program|generate|develop|design|whip up|put together|spin up)\b"
                      rf"(?:(?!\b(?:image|picture|photo|video(?! ?games?)|logo|essay|email|report|poem|story)\b).){{0,80}}?\b({_THINGS})s?\b", re.I)
WANT_GAME_RE = re.compile(r"^\s*(?:hey |so |ok(?:ay)? |yo )?(?:i (?:want|need|wanna)|i'?d like|let'?s (?:make|build|do|create)|can (?:i|we) (?:get|have|make))"
                          r"(?: to (?:make|build|play|have))?(?: me| us)? (?:a|an|my own|some)\b.{0,60}?\b(?:video ?)?games?\b", re.I)
NOT_BUILD_RE = re.compile(r"\b(image|picture|photo|drawing of|video|essay|poem|story|email|report on)\b", re.I)
CHANGE_RE = re.compile(r"^\s*(please |can you |could you |now |ok(ay)? |also |and )*(make|change|add|remove|delete|fix|turn|let|give|use|put|"
                       r"move|speed|slow|increase|decrease|replace|rename|swap|set|show|hide|include|improve|polish|update|tweak|"
                       r"adjust|resize|recolor|rewrite|redo|try|undo|it should|the \w+ should|i want|i'd like|i would like)\b", re.I)
RUN_RE = re.compile(r"^\s*(please |can you |could you |now )*(test|check|debug)( it| this| the (game|app|preview|site))?( again| for (errors|bugs))?\s*[.!?]*$", re.I)
LAUNCH_RE = re.compile(r"^\s*(please |can you |could you |now |ok(?:ay)? |let'?s )*(launch|open|start|play|run|fire up|boot up)( up)?"
                       r"( it| this| that| the (game|app|site|page|thing)| my (game|app))?( (up|in|as) (a |its own |the )?(window|app|browser))?( again| now)?\s*[.!?]*$", re.I)
_IT = r"(?:it|this|that|the (?:game|app|project|folder|thing)|(?:the |its |the game'?s? |the app'?s? )?folder|my (?:game|app))"
MANAGE_RE = re.compile(rf"^\s*(?:please |can you |could you |now |ok(?:ay)? |also |and )*(?:"
                       rf"(?P<move>move|put) {_IT}(?: folder)? (?:to|into|in|onto|on) (?P<mdst>.+)|"
                       rf"(?P<copy>copy|duplicate) {_IT}(?: folder)?(?: (?:to|into|in|onto|on) (?P<cdst>.+))?|"
                       rf"(?P<rename>rename) {_IT}(?: folder)?(?: to| as)? (?P<newname>.+)|"
                       rf"(?P<trash>delete|trash|remove|get rid of|throw away) {_IT}(?: folder)?|"
                       rf"(?P<zip>zip|compress)(?: up)? {_IT}(?: up)?|"
                       rf"(?P<reveal>(?:show|reveal|find) {_IT} in (?:the )?finder|open (?:the |its |the game'?s? |the app'?s? )?folder|where(?:'s| is) {_IT}(?: saved)?)|"
                       rf"open (?:{_IT}|(?P<file>[\w.-]+\.(?:js|html|css|json|md))) (?:in|with) (?P<app>[\w .-]+?)"
                       rf")\s*[.!?]*$", re.I)
YES_RE = re.compile(r"^\s*(yes|yeah|yep|sure|ok(ay)?|do it|go ahead|confirm(ed)?|delete it|trash it)\b", re.I)
FIX_PREVIEW_RE = re.compile(r"\b(fix|repair)\b.*\b(error|errors|bug|bugs|console|preview|broken|crash|it)\b", re.I)
NEW_RE = re.compile(r"\b(new|another|different|separate|second)\b|^\s*(?:please |can you |could you |now |ok(?:ay)? |also )*"
                    r"(?:make|build|create|code|write|program|generate|develop|design|whip up|put together|spin up)(?: me| us)? (?:a|an)\b", re.I)
MARK = "<!-- xod-build:"
PLACE_RE = re.compile(r"\b(?:in|inside|into|under|on)\s+(?:my\s+|the\s+)?(documents|desktop|downloads)\b", re.I)
ART_RE = re.compile(r"\b(images?|sprites?|textures?|art ?work|artwork|art|pictures?|backgrounds?|illustrat\w*|graphics|pixel art|photos?|portraits?|characters? art)\b", re.I)

ART_PLAN = """
- The user wants real images. Add "images": up to 3 of {"file": "assets/name.png", "prompt": "what to paint, style, plain background if it's a sprite"}.
  The code will load them from those paths (e.g. new Image(), THREE.TextureLoader, CSS background-image)."""

PLAN_PROMPT = """You plan small apps and games that run in a web browser. Reply with JSON only:
{"title": "Short name", "folder": "short-kebab-folder-name", "summary": "One sentence: what it is.",
 "features": ["6 to 12 concrete must-haves, each one testable, e.g. 'enemies shoot back and lower your health'"],
 "files": [{"path": "main.js", "purpose": "...and which features it holds"}, {"path": "index.html", "purpose": "..."}],
 "how_to_use": "One line: controls or how to use it."}

Rules:
- Plain HTML, CSS and JavaScript. No build step, no npm, no frameworks that need installing.
- Always index.html.
- Games: use three.js unless the user names something else (plain canvas, a 2D library…). 3D gets a perspective
  camera; a 2D game can use three.js with an OrthographicCamera. Set "three": true in the JSON when you use it.
- three.js: `import * as THREE from 'three'` (and 'three/addons/controls/OrbitControls.js' or
  'three/addons/controls/PointerLockControls.js'); it is available offline. Load main.js as <script type="module">.
- Make art in code: shapes, colors, gradients, canvas drawing, CSS. Don't use image or sound files unless listed below.
- Make the whole thing in one go, playable right away: a start screen, the full core loop, a HUD (score, health…),
  game over and restart. Never a skeleton, a folder layout or "step 1".
- "Like <a famous game>": build that game's core loop in miniature. A shooter like Call of Duty = first-person with
  PointerLockControls, WASD + mouse look, shooting with hit detection (Raycaster), enemies that move toward you and
  shoot back, health and ammo with reload, cover/walls, waves that get harder. Split it across up to 4 files
  (e.g. main.js, world.js, enemies.js) so each one is written well.
- Otherwise 1 to 3 files: index.html (page + its CSS in a <style> tag) and main.js (all the logic) is best."""

FILE_PROMPT = """You are an expert web and game developer. Write the complete file `{path}` for the project below.

Reply with ONLY the file, in one ```{lang} code block. No explanation before or after.
It must work as is: no placeholders, no TODOs, no "..." or "rest of code here".
Use exactly these file names, and only these files: {files}.
Build every item in the project's "features" list that belongs in this file, for real: a HUD shows live numbers
that change, enemies act, counters count. Nothing static, faked or left for later.
{extra}Under 550 lines. Clean, readable, commented where it helps."""

THREE_RULES = """For 3D: `import * as THREE from 'three';` and addons from 'three/addons/...'. Don't add an import map or a CDN link.
Resize the renderer with the window. Use requestAnimationFrame. Put lights in the scene.
"""
HTML_RULES = """index.html loads the other files with relative paths (style.css, main.js). If main.js uses import, load it with
<script type="module" src="main.js"></script>. Include <meta name="viewport" content="width=device-width, initial-scale=1">.
"""

CHANGE_PROMPT = """You are an expert web and game developer working on an existing project.

Reply with small, exact edits in this format, one block per change:

FILE: main.js
<<<<<<< SEARCH
the existing lines, copied exactly (enough of them to be unique)
=======
the new lines
>>>>>>> REPLACE

Rules:
- SEARCH must match the current file exactly, character for character. Keep each block small.
- Only change what's needed; everything else must keep working.
- A brand-new file: FILE: name, then the complete file in one ``` code block (and add it to index.html).
- Plain HTML/CSS/JS, no build step. localStorage works.
{three}"""

FULL_PROMPT = """You are an expert web and game developer. Rewrite the file `{path}` in full with the requested change.
Reply with ONLY the complete file in one ```{lang} code block: no "..." or "unchanged" parts. Keep everything else working.
{three}"""


def lang_of(path: str) -> str:
    return {".html": "html", ".css": "css", ".js": "javascript", ".mjs": "javascript", ".json": "json", ".svg": "svg"}.get(Path(path).suffix.lower(), "")


def slugify(s: str, fallback: str = "app") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40].strip("-")
    return s or fallback


def safe_rel(path: str) -> Optional[str]:
    p = (path or "").strip().strip("`'\"")
    while p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or ".." in Path(p).parts or any(x.startswith(".") for x in Path(p).parts):
        return None
    return p if Path(p).suffix.lower() in ALLOWED_EXT else None


def extract_code(text: str) -> Optional[str]:
    """The body of the (largest) fenced code block; tolerates a missing closing fence."""
    blocks = re.findall(r"```[\w+-]*[^\n]*\n(.*?)(?:```|\Z)", text or "", re.S)
    blocks = [b for b in blocks if b.strip()]
    if blocks:
        return max(blocks, key=len).rstrip() + "\n"
    t = (text or "").strip()
    return t + "\n" if t.startswith(("<", "/", "import", "const", "let", "function", "(", "*", "body", ":root", "html")) else None


EDIT_RE = re.compile(r"FILE:?\s*`?([\w./ -]+?\.\w+)`?\**\s*\n((?:\s*<{5,}\s*SEARCH.*?>{5,}\s*REPLACE[^\n]*\n?)+)", re.S | re.I)
BLOCK_RE = re.compile(r"<{5,}\s*SEARCH[^\n]*\n(.*?)\n?={5,}[^\n]*\n(.*?)\n?>{5,}\s*REPLACE", re.S)


def extract_edits(text: str) -> Dict[str, List[Tuple[str, str]]]:
    out: Dict[str, List[Tuple[str, str]]] = {}
    for m in EDIT_RE.finditer(text or ""):
        rel = safe_rel(m.group(1))
        if rel:
            out.setdefault(rel, []).extend(BLOCK_RE.findall(m.group(2)))
    return out


def apply_edit(src: str, search: str, replace: str) -> Optional[str]:
    """One SEARCH/REPLACE block, matched on whole lines; tolerant of indentation and trailing spaces
    (the replacement takes the file's indentation). None if it doesn't match."""
    if not search.strip():
        return src.rstrip("\n") + "\n" + replace + "\n"
    i = src.find(search)
    if i >= 0 and (i == 0 or src[i - 1] == "\n") and (i + len(search) == len(src) or src[i + len(search)] == "\n" or search.endswith("\n")):
        return src[:i] + replace + src[i + len(search):]
    want = [l.strip() for l in search.strip("\n").splitlines()]
    lines = src.splitlines()
    for i in range(len(lines) - len(want) + 1):
        if all(lines[i + j].strip() == want[j] for j in range(len(want))):
            new = replace.strip("\n").splitlines()
            have = len(lines[i]) - len(lines[i].lstrip())
            first = next((l for l in new if l.strip()), "")
            shift = have - (len(first) - len(first.lstrip()))
            if shift > 0:
                new = [(" " * shift + l) if l.strip() else l for l in new]
            elif shift < 0:
                new = [l[min(-shift, len(l) - len(l.lstrip())):] for l in new]
            return "\n".join(lines[:i] + new + lines[i + len(want):]) + "\n"
    return None


def extract_files(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for m in re.finditer(r"(?:^|\n)\s*(?:#+\s*)?(?:\*\*)?FILE:?\s*`?([\w./ -]+?\.\w+)`?(?:\*\*)?\s*\n+\s*```[\w+-]*[^\n]*\n(.*?)(?:```|\Z)", text or "", re.S | re.I):
        rel = safe_rel(m.group(1))
        if rel and m.group(2).strip():
            out[rel] = m.group(2).rstrip() + "\n"
    return out


OTHER_ENGINE_RE = re.compile(r"\b(2d canvas|plain canvas|canvas only|no three|without three|phaser|pixi|kaboom|p5(\.js)?|babylon|"
                             r"unity|godot|pygame|vanilla js only)\b", re.I)


def is_three(spec: Dict[str, Any], query: str) -> bool:
    """three.js unless asked otherwise: 3D always, and games by default (the user's pick)."""
    blob = (json.dumps(spec) + " " + query).lower()
    if spec.get("three") is True or re.search(r"\b3d\b|three\.?js|\b3js\b|webgl|first[- ]person|third[- ]person", blob):
        return True
    return bool(re.search(r"\bgames?\b", query, re.I)) and not OTHER_ENGINE_RE.search(query)


def wants_build(query: str) -> bool:
    q = (query or "").strip()
    if q and len(q) < 400 and WANT_GAME_RE.search(q) and not re.search(r"\b(recommend|suggest|buy|download|steam|xbox|playstation|switch|best)\b", q, re.I):
        return True
    return bool(q) and len(q) < 1500 and bool(BUILD_RE.search(q)) and not (NOT_BUILD_RE.search(q) and not re.search(r"\b(game|app)\b", q, re.I))


def last_build(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The app the most recent builder answer in this chat was about."""
    for m in reversed(messages[:-1]):
        c = m.get("content")
        if m.get("role") == "assistant" and isinstance(c, str) and MARK in c:
            try:
                return json.loads(c.split(MARK, 1)[1].split("-->", 1)[0])
            except ValueError:
                return None
    return None


def route(query: str, messages: List[Dict[str, Any]], workspace: Optional[str]) -> Optional[Dict[str, Any]]:
    """Should the builder take this message, and on which app? None if not."""
    q = (query or "").strip()
    prev = last_build(messages)
    app_dir = Path(prev["path"]) if prev and prev.get("path") and Path(prev["path"]).is_dir() else None
    if not app_dir and workspace:
        apps = previews.find_apps(workspace)
        if apps and (MANAGE_RE.search(q) or CHANGE_RE.search(q) or RUN_RE.search(q) or LAUNCH_RE.search(q) or FIX_PREVIEW_RE.search(q)) and not wants_build(q):
            app_dir = Path(apps[0]["path"])
    if app_dir:
        if prev and prev.get("pending") == "trash" and YES_RE.search(q):
            return {"mode": "manage", "dir": app_dir, "confirmed": True}
        if LAUNCH_RE.search(q):   # first: "open it in its own window" is a launch, not "open it in <an app>"
            return {"mode": "launch", "dir": app_dir}
        if MANAGE_RE.search(q):
            return {"mode": "manage", "dir": app_dir}
        if RUN_RE.search(q):
            return {"mode": "run", "dir": app_dir}
        if FIX_PREVIEW_RE.search(q) and not wants_build(q):
            return {"mode": "change", "dir": app_dir}
        if wants_build(q) and NEW_RE.search(q):
            return {"mode": "new"}
        if CHANGE_RE.search(q) or (wants_build(q) and not NEW_RE.search(q)):
            return {"mode": "change", "dir": app_dir}
        return None
    if wants_build(q):
        return {"mode": "new"}
    return None


class Builder:
    def __init__(self, execute_tool, spoken: bool = False):
        self.execute_tool = execute_tool
        self.spoken = spoken
        self.n = 0

    # ---------------------------------------------------------------- helpers

    async def _write(self, path: Path, content: str) -> AsyncGenerator[Dict[str, Any], None]:
        self.n += 1
        cid = f"build_{self.n}"
        args = {"filepath": str(path), "content": content}
        yield {"type": "tool_call", "name": "computer_write_file", "args": {"filepath": str(path), "content": f"({len(content.splitlines())} lines)"}, "id": cid}
        res = await self.execute_tool("computer_write_file", args)
        ok = isinstance(res, dict) and res.get("success", True) and not res.get("error")
        yield {"type": "tool_result", "name": "computer_write_file", "id": cid,
               "summary": f"Wrote {path.name} ({len(content.splitlines())} lines)" if ok else f"Couldn't write {path.name}",
               "data": {"success": ok, "path": str(path), "lines": len(content.splitlines()), "error": (res or {}).get("error")}}
        if not ok:
            raise RuntimeError(f"Couldn't write {path.name}: {(res or {}).get('error')}")

    async def _stream_code(self, messages: List[Dict[str, Any]], label: str, max_tokens: int = 9000) -> AsyncGenerator[Dict[str, Any], None]:
        """Streams a model reply, showing progress as it grows; the full text arrives as {"type": "_text"}."""
        text, last = "", 0.0
        async for ch in llm_service.stream_chat_completion(messages=messages, temperature=0.25, max_tokens=max_tokens,
                                                           think=False, task="builder"):
            if ch.get("type") == "content":
                text += ch.get("content", "")
                if time.time() - last > 0.6:
                    last = time.time()
                    yield {"type": "status", "content": f"{label}… {text.count(chr(10))} lines"}
            elif ch.get("type") == "error":
                raise RuntimeError(ch.get("content") or "the model stopped")
        yield {"type": "_text", "text": text}

    @staticmethod
    def _files_block(app_dir: Path, limit: int = 40000) -> str:
        parts, used = [], 0
        for f in previews.list_files(previews.register(app_dir)):
            if not f["text"] or f["path"] == previews.APP_MARK:
                continue
            body = (app_dir / f["path"]).read_text(errors="replace")
            if used + len(body) > limit:
                body = body[: max(2000, limit - used)] + "\n/* (cut for length) */"
            used += len(body)
            parts.append(f"FILE: {f['path']}\n```{lang_of(f['path'])}\n{body}\n```")
        return "\n\n".join(parts)

    def _mark(self, app_dir: Path, info: Dict[str, Any]) -> None:
        mark = app_dir / previews.APP_MARK
        old = {}
        try:
            old = json.loads(mark.read_text())
        except (OSError, ValueError):
            pass
        mark.write_text(json.dumps({**old, **info, "updated": time.time()}, indent=1))

    # ---------------------------------------------------------------- run and fix

    async def _edit(self, app_dir: Path, task: str, three: bool, label: str) -> AsyncGenerator[Dict[str, Any], None]:
        """Asks for SEARCH/REPLACE edits and applies them; a file whose edits don't match is rewritten in full.
        Ends with {"type": "_changed", "files": [...]}."""
        msgs = [{"role": "system", "content": CHANGE_PROMPT.format(three=THREE_RULES if three else "")},
                {"role": "user", "content": task}]
        text = ""
        async for ev in self._stream_code(msgs, label, max_tokens=6000):
            if ev["type"] == "_text":
                text = ev["text"]
            else:
                yield ev
        changed: List[str] = []
        new_files = {rel: body for rel, body in extract_files(text).items() if not (app_dir / rel).exists()}
        retry: List[str] = []
        for rel, blocks in extract_edits(text).items():
            path = app_dir / rel
            if not path.is_file():
                continue
            src = path.read_text(errors="replace")
            ok = True
            for search, replace in blocks:
                out = apply_edit(src, search, replace)
                if out is None:
                    ok = False
                    break
                src = out
            if ok and blocks:
                async for ev in self._write(path, src[:MAX_FILE_CHARS]):
                    yield ev
                changed.append(rel)
            else:
                retry.append(rel)
        # Whole files the model sent instead of edits (common with small models): take them as they are.
        for rel, body in extract_files(text).items():
            if rel not in new_files and rel not in changed and (app_dir / rel).is_file() and len(body) > 200:
                async for ev in self._write(app_dir / rel, body[:MAX_FILE_CHARS]):
                    yield ev
                changed.append(rel)
                retry = [r for r in retry if r != rel]
        for rel, body in new_files.items():
            async for ev in self._write(app_dir / rel, body[:MAX_FILE_CHARS]):
                yield ev
            changed.append(rel)
        for rel in retry:
            yield {"type": "status", "content": f"Rewriting {rel} in full…"}
            msgs = [{"role": "system", "content": FULL_PROMPT.format(path=rel, lang=lang_of(rel), three=THREE_RULES if three else "")},
                    {"role": "user", "content": task + f"\n\nNow write the complete new `{rel}`."}]
            body = ""
            async for ev in self._stream_code(msgs, f"Rewriting {rel}"):
                if ev["type"] == "_text":
                    body = extract_code(ev["text"]) or ""
                else:
                    yield ev
            if body.strip():
                async for ev in self._write(app_dir / rel, body[:MAX_FILE_CHARS]):
                    yield ev
                changed.append(rel)
        yield {"type": "_changed", "files": changed}

    async def _run_and_fix(self, app_dir: Path, request: str, three: bool, rounds: int = 2) -> AsyncGenerator[Dict[str, Any], None]:
        url = previews.url_for(app_dir)
        result: Dict[str, Any] = {}
        for attempt in range(rounds + 1):
            yield {"type": "status", "content": "Running it to check for errors…" if attempt == 0 else "Running it again…"}
            self.n += 1
            cid = f"build_{self.n}"
            yield {"type": "tool_call", "name": "preview_check", "args": {"url": url}, "id": cid}
            result = await previews.check(url)
            problems = result.get("errors", []) + [f"Couldn't load {f}" for f in result.get("failed", [])]
            if result.get("blank"):
                problems.append("The page is blank: nothing visible was drawn.")
            summary = ("Ran it: no errors" if result.get("ok") else
                       f"Ran it: {len(problems)} problem{'s' if len(problems) != 1 else ''}" if problems else
                       f"Ran it: {(result.get('note') or 'no errors, but the check was cut short').rstrip('.')}") \
                if result.get("ran") else (result.get("note") or "Couldn't run it")
            yield {"type": "tool_result", "name": "preview_check", "id": cid, "summary": summary, "data": result}
            # Nothing concrete to fix (e.g. the check timed out): rewriting code blind only makes it worse.
            if not result.get("ran") or result.get("ok") or not problems or attempt == rounds:
                break
            yield {"type": "status", "content": f"Fixing {len(problems)} problem{'s' if len(problems) != 1 else ''}…"}
            task = (f"The user asked for: {request}\n\nWhen run in a browser it has these problems:\n- " + "\n- ".join(problems[:10]) +
                    f"\n\nThe project:\n\n{self._files_block(app_dir)}\n\nFix the problems.")
            changed: List[str] = []
            async for ev in self._edit(app_dir, task, three, "Fixing"):
                if ev["type"] == "_changed":
                    changed = ev["files"]
                else:
                    yield ev
            if not changed:
                break
        yield {"type": "_check", "result": result}

    # ---------------------------------------------------------------- the three entry points

    async def new(self, query: str, workspace: Optional[str]) -> AsyncGenerator[Dict[str, Any], None]:
        yield {"type": "plan", "title": "Building it", "steps": [
            {"id": 1, "title": "Plan the files", "tools": [], "status": "in_progress"},
            {"id": 2, "title": "Write the code", "tools": ["computer_write_file"], "status": "pending"},
            {"id": 3, "title": "Run it and fix any errors", "tools": ["preview_check"], "status": "pending"},
        ]}
        yield {"type": "status", "content": "Planning the files…"}
        art_wanted = bool(ART_RE.search(query))
        art_ready = art_wanted and image_generation_ready()
        spec = await self._plan(query, art_ready)
        three = is_three(spec, query)
        base, project = self._where(workspace, spec, query)
        app_dir = self._free_dir(base / slugify(spec.get("folder") or spec.get("title")))
        app_dir.mkdir(parents=True, exist_ok=True)
        files = [f for f in spec["files"] if safe_rel(f.get("path", ""))][:5]
        if not any(f["path"] == "index.html" for f in files):
            files.insert(0, {"path": "index.html", "purpose": "the page"})
        # index.html last: it should load exactly what the other files turned out to be.
        files.sort(key=lambda f: f["path"] == "index.html")
        yield {"type": "plan_step", "step_id": 1, "status": "completed"}
        yield {"type": "plan_step", "step_id": 2, "status": "in_progress"}
        yield {"type": "build_started", "title": spec.get("title"), "path": str(app_dir), "url": previews.url_for(app_dir),
               "root": previews.register(app_dir), "project": project}
        assets: List[str] = []
        if art_ready and isinstance(spec.get("images"), list) and spec["images"]:
            async for ev in self._make_art(app_dir, spec["images"][:3]):
                if ev["type"] == "_assets":
                    assets = ev["files"]
                else:
                    yield ev
        spec.pop("images", None)
        names = ", ".join(f["path"] for f in files) + (f" (and these images, already made: {', '.join(assets)})" if assets else "")
        self.art_note = ("" if not art_wanted or assets else
                         "Image generation isn't set up yet (Tools → Media Studio), so I drew the art in code." if not art_ready else
                         "I couldn't make the images this time, so I drew the art in code.")
        written: Dict[str, str] = {}
        for f in files:
            rel = safe_rel(f["path"])
            extra = (THREE_RULES if three and rel.endswith((".js", ".html")) else "") + (HTML_RULES if rel == "index.html" else "")
            context = [f"Request: {query}", f"Project: {json.dumps(spec)[:3000]}"]
            for done_rel, body in written.items():
                context.append(f"Already written, FILE: {done_rel}\n```{lang_of(done_rel)}\n{body[:20000]}\n```")
            msgs = [{"role": "system", "content": FILE_PROMPT.format(path=rel, lang=lang_of(rel), files=names, extra=extra)},
                    {"role": "user", "content": "\n\n".join(context) + f"\n\nNow write `{rel}` ({f.get('purpose', '')}) in full."}]
            text = ""
            async for ev in self._stream_code(msgs, f"Writing {rel}"):
                if ev["type"] == "_text":
                    text = ev["text"]
                else:
                    yield ev
            code = extract_code(text)
            if not code:
                raise RuntimeError(f"The model didn't write {rel}.")
            written[rel] = code[:MAX_FILE_CHARS]
            async for ev in self._write(app_dir / rel, written[rel]):
                yield ev
        self._mark(app_dir, {"title": spec.get("title"), "summary": spec.get("summary"), "how_to_use": spec.get("how_to_use"),
                             "entry": "index.html", "three": three, "request": query[:500], "created": time.time()})
        yield {"type": "plan_step", "step_id": 2, "status": "completed"}
        yield {"type": "plan_step", "step_id": 3, "status": "in_progress"}
        check: Dict[str, Any] = {}
        async for ev in self._run_and_fix(app_dir, query, three):
            if ev["type"] == "_check":
                check = ev["result"]
            else:
                yield ev
        yield {"type": "plan_step", "step_id": 3, "status": "completed"}
        async for ev in self._finish(app_dir, "new", check, project):
            yield ev

    async def change(self, query: str, app_dir: Path) -> AsyncGenerator[Dict[str, Any], None]:
        info = self._info(app_dir)
        three = bool(info.get("three"))
        yield {"type": "plan", "title": f"Changing {info.get('title') or app_dir.name}", "steps": [
            {"id": 1, "title": "Rewrite what needs to change", "tools": ["computer_write_file"], "status": "in_progress"},
            {"id": 2, "title": "Run it and fix any errors", "tools": ["preview_check"], "status": "pending"},
        ]}
        yield {"type": "build_started", "title": info.get("title"), "path": str(app_dir), "url": previews.url_for(app_dir),
               "root": previews.register(app_dir)}
        task = (f"The app: {info.get('title') or app_dir.name}. {info.get('summary') or ''}\n\n{self._files_block(app_dir)}\n\n"
                f"The user's change request: {query}")
        files: List[str] = []
        async for ev in self._edit(app_dir, task, three, "Writing the changes"):
            if ev["type"] == "_changed":
                files = ev["files"]
            else:
                yield ev
        if not files:
            yield {"type": "content", "content": "I couldn't work out the change from that. Could you say a bit more about what should be different?"}
            yield {"type": "done"}
            return
        self._mark(app_dir, {"last_change": query[:300]})
        yield {"type": "plan_step", "step_id": 1, "status": "completed"}
        yield {"type": "plan_step", "step_id": 2, "status": "in_progress"}
        check: Dict[str, Any] = {}
        async for ev in self._run_and_fix(app_dir, query, three):
            if ev["type"] == "_check":
                check = ev["result"]
            else:
                yield ev
        yield {"type": "plan_step", "step_id": 2, "status": "completed"}
        async for ev in self._finish(app_dir, "change", check, None, changed=list(files)):
            yield ev

    async def launch(self, app_dir: Path) -> AsyncGenerator[Dict[str, Any], None]:
        """Opens the app for real: its own window in the desktop app, or a browser tab."""
        info = self._info(app_dir)
        title = info.get("title") or app_dir.name
        url = previews.url_for(app_dir)
        yield {"type": "launch_app", "title": title, "url": url, "root": previews.register(app_dir), "path": str(app_dir)}
        tip = f" {info['how_to_use']}" if info.get("how_to_use") and not self.spoken else ""
        yield {"type": "content", "content": f"**Launched {title}** in its own window.{tip}\n\n" + MARK +
               json.dumps({"path": str(app_dir), "title": title}) + " -->"}
        yield {"type": "done"}

    async def run(self, app_dir: Path) -> AsyncGenerator[Dict[str, Any], None]:
        info = self._info(app_dir)
        yield {"type": "build_started", "title": info.get("title"), "path": str(app_dir), "url": previews.url_for(app_dir),
               "root": previews.register(app_dir)}
        check: Dict[str, Any] = {}
        async for ev in self._run_and_fix(app_dir, info.get("request") or "", bool(info.get("three")), rounds=0):
            if ev["type"] == "_check":
                check = ev["result"]
            else:
                yield ev
        async for ev in self._finish(app_dir, "run", check, None):
            yield ev

    # ---------------------------------------------------------------- moving it around

    async def _tool(self, name: str, args: Dict[str, Any], done: str) -> AsyncGenerator[Dict[str, Any], None]:
        self.n += 1
        cid = f"build_{self.n}"
        yield {"type": "tool_call", "name": name, "args": args, "id": cid}
        res = await self.execute_tool(name, args)
        ok = isinstance(res, dict) and res.get("success", True) and not res.get("error")
        yield {"type": "tool_result", "name": name, "id": cid, "summary": done if ok else f"Couldn't: {(res or {}).get('error')}", "data": res}
        yield {"type": "_ok", "ok": ok, "error": (res or {}).get("error")}

    @staticmethod
    def _place(text: str, app_dir: Path) -> Optional[Path]:
        """'my desktop', 'documents', 'a folder called games', 'a games folder on my desktop', '~/Projects'."""
        t = re.sub(r"[.!?]+$", "", text.strip()).strip().strip("\"'")
        home = Path.home()
        named = {"desktop": home / "Desktop", "documents": home / "Documents", "downloads": home / "Downloads", "home": home}
        m = re.search(r"\b(?:a |the )?(?:new )?folder (?:called|named) [\"']?([\w .-]{1,60}?)[\"']?(?: (?:in|on|inside) (?:my |the )?(\w+))?$", t, re.I) or \
            re.search(r"\b(?:a |the |my )?(?:new )?[\"']?([\w.-]{1,40})[\"']? folder(?: (?:in|on|inside) (?:my |the )?(\w+))?$", t, re.I)
        if m:
            base = named.get((m.group(2) or "").lower(), app_dir.parent)
            return base / m.group(1).strip()
        m = re.fullmatch(r"(?:my |the )?(desktop|documents|downloads|home)(?: folder)?", t, re.I)
        if m:
            return named[m.group(1).lower()]
        if t.startswith(("/", "~")):
            return Path(t).expanduser()
        return None

    @staticmethod
    def _safe_target(p: Path) -> bool:
        try:
            r = p.expanduser().resolve()
            home = Path.home().resolve()
            rel = r.relative_to(home)
        except (ValueError, OSError):
            return str(p).startswith(("/Volumes/", "/tmp/", "/private/tmp/"))
        return not (rel.parts and (rel.parts[0] in ("Library", "Applications") or rel.parts[0].startswith(".")))

    def _done(self, text: str, app_dir: Path, title: str, **extra) -> Dict[str, Any]:
        return {"type": "content", "content": text + "\n\n" + MARK + json.dumps({"path": str(app_dir), "title": title, **extra}) + " -->"}

    async def manage(self, query: str, app_dir: Path, confirmed: bool = False) -> AsyncGenerator[Dict[str, Any], None]:
        """Moves, copies, renames, zips, trashes, shows or opens the app's folder, with Xod's own file tools
        (so every change is in Activity with Undo)."""
        info = self._info(app_dir)
        title = info.get("title") or app_dir.name
        m = MANAGE_RE.search(query) if not confirmed else None
        ok_ev: Dict[str, Any] = {}

        async def run(name, args, done):
            async for ev in self._tool(name, args, done):
                if ev["type"] == "_ok":
                    ok_ev.clear(); ok_ev.update(ev)
                else:
                    yield ev

        if confirmed or (m and m.group("trash")):
            if not confirmed:
                yield self._done(f"That moves **{title}** (`{self._short(app_dir)}`) to the Trash. You can put it back from Activity. "
                                 f"Say **yes** to go ahead.", app_dir, title, pending="trash")
                yield {"type": "done"}
                return
            async for ev in run("computer_manage_file", {"action": "trash", "src": str(app_dir)}, f"Moved {app_dir.name} to the Trash"):
                yield ev
            yield {"type": "content", "content": f"**Moved {title} to the Trash.** Undo in Activity puts it back." if ok_ev.get("ok")
                   else f"I couldn't move it to the Trash: {ok_ev.get('error')}"}
            yield {"type": "done"}
            return

        if m.group("move") or m.group("copy") or m.group("rename"):
            if m.group("rename"):
                name = re.sub(r"[\\/:*?\"<>|]+", "", re.sub(r"[.!?]+$", "", m.group("newname").strip()).strip("\"'")).strip()
                dst = app_dir.parent / name if name else None
            else:
                where = self._place(m.group("mdst") or m.group("cdst") or "", app_dir) if (m.group("mdst") or m.group("cdst")) else app_dir.parent
                dst = (where / (app_dir.name if not m.group("copy") or where != app_dir.parent else app_dir.name + " copy")) if where else None
            if not dst:
                yield self._done("Where should it go? Say something like \"move it to my Desktop\" or \"into a folder called Games\".", app_dir, title)
                yield {"type": "done"}
                return
            if not self._safe_target(dst):
                yield self._done(f"I keep apps inside your home folder (not Library or hidden folders), so I didn't put it in `{dst}`.", app_dir, title)
                yield {"type": "done"}
                return
            if dst.exists():
                dst = self._free_dir(dst)
            if not dst.parent.exists():
                async for ev in run("computer_manage_file", {"action": "mkdir", "src": str(dst.parent)}, f"Made the folder {dst.parent.name}"):
                    yield ev
            action = "copy" if m.group("copy") else "move"
            verb = {"copy": "Copied", "move": "Renamed" if m.group("rename") else "Moved"}[action]
            async for ev in run("computer_manage_file", {"action": action, "src": str(app_dir), "dst": str(dst)}, f"{verb} {app_dir.name} to {self._short(dst)}"):
                yield ev
            if not ok_ev.get("ok"):
                yield self._done(f"I couldn't do that: {ok_ev.get('error')}", app_dir, title)
                yield {"type": "done"}
                return
            new_dir = dst if action == "move" else app_dir
            if action == "move":
                previews.register(new_dir)
            yield {"type": "build_done", "title": title, "path": str(new_dir), "url": previews.url_for(new_dir), "root": previews.register(new_dir)}
            where_txt = f"`{self._short(dst)}`"
            msg = {"Copied": f"**Copied {title}** to {where_txt}. The copy is its own app; I'll keep working on the original.",
                   "Moved": f"**Moved {title}** to {where_txt}. Everything still works from there, and Undo in Activity moves it back.",
                   "Renamed": f"**Renamed it** to {where_txt}. Undo in Activity changes it back."}[verb]
            yield self._done(msg, new_dir, title)
            yield {"type": "done"}
            return

        if m.group("zip"):
            import shutil as _sh
            zip_base, i = app_dir.parent / app_dir.name, 2
            while zip_base.with_name(zip_base.name + ".zip").exists():
                zip_base, i = app_dir.parent / f"{app_dir.name}-{i}", i + 1
            self.n += 1
            cid = f"build_{self.n}"
            yield {"type": "tool_call", "name": "computer_zip", "args": {"folder": str(app_dir)}, "id": cid}
            out = await asyncio.to_thread(_sh.make_archive, str(zip_base), "zip", str(app_dir.parent), app_dir.name)
            yield {"type": "tool_result", "name": "computer_zip", "id": cid, "summary": f"Made {Path(out).name}", "data": {"success": True, "path": out}}
            yield self._done(f"**Zipped it:** `{self._short(Path(out))}`, ready to send. Whoever opens it just double-clicks `index.html`"
                             f"{' (three.js is loaded from Xod, so it needs Xod or a copy of three.js next to it)' if info.get('three') else ''}.", app_dir, title)
            yield {"type": "done"}
            return

        if m.group("reveal"):
            async for ev in run("computer_reveal_finder", {"path": str(app_dir)}, f"Showed {app_dir.name} in Finder"):
                yield ev
            yield self._done(f"**{title}** is in `{self._short(app_dir)}`; I opened it in Finder.", app_dir, title)
            yield {"type": "done"}
            return

        # open it / a file in an app
        app = (m.group("app") or "").strip().lower()
        apps = {"chrome": "Google Chrome", "google chrome": "Google Chrome", "safari": "Safari", "firefox": "Firefox", "arc": "Arc",
                "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "code": "Visual Studio Code", "visual studio code": "Visual Studio Code",
                "cursor": "Cursor", "textedit": "TextEdit", "finder": "Finder", "xcode": "Xcode", "sublime": "Sublime Text", "zed": "Zed"}
        app_name = apps.get(app, app.title())
        browser = app_name in ("Google Chrome", "Safari", "Firefox", "Arc")
        if browser:
            base = previews.SERVER_BASE or "http://127.0.0.1:" + os.environ.get("XOD_PORT", "8000")
            target = base + previews.url_for(app_dir)
        else:
            f = m.group("file")
            target = str(app_dir / safe_rel(f)) if f and safe_rel(f) and (app_dir / safe_rel(f)).is_file() else str(app_dir)
        async for ev in run("computer_open_item", {"target": target, "app_name": app_name}, f"Opened it in {app_name}"):
            yield ev
        yield self._done(f"**Opened {Path(target).name if not browser and target != str(app_dir) else title}** in {app_name}." if ok_ev.get("ok")
                         else f"I couldn't open it in {app_name}: {ok_ev.get('error')}", app_dir, title)
        yield {"type": "done"}

    @staticmethod
    def _short(p: Path) -> str:
        home = str(Path.home())
        return "~" + str(p)[len(home):] if str(p).startswith(home) else str(p)

    # ---------------------------------------------------------------- pieces

    async def _plan(self, query: str, art: bool = False) -> Dict[str, Any]:
        spec: Dict[str, Any] = {}
        try:
            msg = await llm_service.chat_completion(messages=[{"role": "system", "content": PLAN_PROMPT + (ART_PLAN if art else "")}, {"role": "user", "content": query}],
                                                    temperature=0.3, max_tokens=900, think=False, task="builder_plan")
            spec = parse_llm_json(msg.get("content") or "") or {}
        except Exception as e:
            logger.warning(f"builder plan failed, using a default: {e}")
        if not isinstance(spec.get("files"), list) or not spec["files"]:
            spec["files"] = [{"path": "index.html", "purpose": "page and styles"}, {"path": "main.js", "purpose": "all the logic"}]
        spec.setdefault("title", re.sub(r"^(make|build|create|code|write)( me)?( a| an)?\s+", "", query, flags=re.I)[:40].strip().capitalize() or "App")
        return spec

    async def _make_art(self, app_dir: Path, images: List[Dict[str, Any]]) -> AsyncGenerator[Dict[str, Any], None]:
        """Paints the images the plan asked for with Media Studio, into the app's assets folder."""
        import shutil as _sh
        made: List[str] = []
        for i, img in enumerate(images):
            prompt = str((img or {}).get("prompt") or "").strip()
            want = str((img or {}).get("file") or f"assets/image-{i + 1}.png")
            if not prompt:
                continue
            yield {"type": "status", "content": f"Painting {Path(want).name} ({i + 1} of {len(images)})…"}
            self.n += 1
            cid = f"build_{self.n}"
            args = {"prompt": prompt, "style": "illustration", "width": 512, "height": 512}
            yield {"type": "tool_call", "name": "media_generate_image", "args": args, "id": cid}
            res = await self.execute_tool("media_generate_image", args)
            out = (res or {}).get("output_path") or (res or {}).get("path")
            if not (res or {}).get("success") or not out or not Path(out).is_file():
                yield {"type": "tool_result", "name": "media_generate_image", "id": cid, "summary": "Couldn't make it",
                       "data": {"success": False, "error": (res or {}).get("error")}}
                continue
            rel = "assets/" + slugify(Path(want).stem, f"image-{i + 1}") + Path(out).suffix.lower()
            (app_dir / "assets").mkdir(exist_ok=True)
            _sh.copy2(out, app_dir / rel)
            made.append(rel)
            yield {"type": "tool_result", "name": "media_generate_image", "id": cid, "summary": f"Painted {rel}",
                   "data": {"success": True, "path": str(app_dir / rel), "markdown": res.get("markdown")}}
        yield {"type": "_assets", "files": made}

    @staticmethod
    def _where(workspace: Optional[str], spec: Dict[str, Any], query: str = "") -> Tuple[Path, Optional[Dict[str, Any]]]:
        m = PLACE_RE.search(query)   # "make a new folder in Documents for it"
        if m:
            base = Path.home() / m.group(1).capitalize()
            if base.is_dir():
                return base, None
        if workspace and Path(workspace).expanduser().is_dir():
            return Path(workspace).expanduser().resolve(), None
        from app.services.project_storage import project_storage_service as project_storage
        title = spec.get("title") or "New app"
        try:
            proj = project_storage.create_project(name=title, description=spec.get("summary") or "Made by Xod's builder", icon="gamepad-2"
                                                  if "game" in json.dumps(spec).lower() else "app-window", color="#f2b24a")
        except Exception:
            proj = project_storage.create_project(name=f"{title} {time.strftime('%H%M%S')}")
        slug = proj.get("slug") or (proj.get("project") or {}).get("slug")
        return project_storage.get_project_dir(slug), {"slug": slug, "name": title}

    @staticmethod
    def _free_dir(d: Path) -> Path:
        if not d.exists():
            return d
        for i in range(2, 100):
            c = d.with_name(f"{d.name}-{i}")
            if not c.exists():
                return c
        return d.with_name(f"{d.name}-{int(time.time())}")

    @staticmethod
    def _info(app_dir: Path) -> Dict[str, Any]:
        try:
            return json.loads((app_dir / previews.APP_MARK).read_text())
        except (OSError, ValueError):
            return {"title": app_dir.name}

    async def _finish(self, app_dir: Path, mode: str, check: Dict[str, Any], project: Optional[Dict[str, Any]],
                      changed: Optional[List[str]] = None) -> AsyncGenerator[Dict[str, Any], None]:
        """The answer, written from what's on disk and what the check measured: nothing invented."""
        info = self._info(app_dir)
        rid = previews.register(app_dir)
        files = [f for f in previews.list_files(rid) if f["path"] != previews.APP_MARK]
        url = previews.url_for(app_dir)
        title = info.get("title") or app_dir.name
        home = str(Path.home())
        where = str(app_dir).replace(home, "~", 1)
        if len(where) > 48:      # the Files tab shows the full path
            where = f"{app_dir.parent.name}/{app_dir.name}"
        problems = check.get("errors", []) + [f"couldn't load {f}" for f in check.get("failed", [])] + (["the page looked blank"] if check.get("blank") else [])
        yield {"type": "build_done", "title": title, "path": str(app_dir), "url": url, "root": rid, "project": project,
               "files": files, "check": check, "mode": mode}
        lines = []
        if mode == "new":
            lines.append(f"**I built {title}.** {info.get('summary') or ''}".strip())
        elif mode == "change":
            lines.append(f"**Updated {title}:** changed {', '.join(f'`{c}`' for c in changed or [])}.")
        else:
            lines.append(f"**Ran {title}.**")
        if self.spoken:
            lines = [lines[0] + (" It's open in the preview." if not problems else f" It still has {len(problems)} error{'s' if len(problems) != 1 else ''}; they're in the preview's console.")]
        else:
            if check.get("ran"):
                lines.append("It's running in the preview on the right; I played it for a few seconds and saw **no errors**." if not problems else
                             f"It's open in the preview on the right. **{len(problems)} problem{'s' if len(problems) != 1 else ''} left** after fixing what I could:\n" +
                             "\n".join(f"- `{p[:200]}`" for p in problems[:6]) + "\n\nSay \"fix the errors\" and I'll have another go.")
            else:
                lines.append(f"It's open in the preview on the right. ({check.get('note') or 'I could not run the check.'})")
            if info.get("how_to_use"):
                lines.append(f"**How to use it:** {info['how_to_use']}")
            lines.append(f"**Files** in `{where}`: " + ", ".join(f"`{f['path']}` ({max(1, round(f['bytes'] / 1024))} KB)" for f in files[:12]))
            if getattr(self, "art_note", ""):
                lines.append(self.art_note)
            if project:
                lines.append(f"I made a project for it, **{project['name']}**, on the left.")
            lines.append("Ask for changes in plain words, like \"make it harder\" or \"add a high score\". Every change can be undone in Activity.")
        marker = MARK + json.dumps({"path": str(app_dir), "title": title}) + " -->"
        yield {"type": "content", "content": "\n\n".join(lines) + "\n\n" + marker}
        yield {"type": "done"}


def image_generation_ready() -> bool:
    """Can Media Studio paint right now (a cloud key, or a local image model that's installed)?"""
    try:
        from app.config import settings
        if settings.openai_api_key:
            return True
        from app.services.media_runtime import list_models
        return any(m.get("usable") and (m.get("kind") or "image") == "image" for m in list_models())
    except Exception:
        return False


async def stream(query: str, messages: List[Dict[str, Any]], workspace: Optional[str], execute_tool,
                 decision: Dict[str, Any], spoken: bool = False) -> AsyncGenerator[Dict[str, Any], None]:
    b = Builder(execute_tool, spoken)
    try:
        if decision["mode"] == "new":
            gen = b.new(query, workspace)
        elif decision["mode"] == "change":
            gen = b.change(query, decision["dir"])
        elif decision["mode"] == "launch":
            gen = b.launch(decision["dir"])
        elif decision["mode"] == "manage":
            gen = b.manage(query, decision["dir"], decision.get("confirmed", False))
        else:
            gen = b.run(decision["dir"])
        async for ev in gen:
            yield ev
    except Exception as e:
        logger.exception("builder failed")
        yield {"type": "content", "content": f"I couldn't finish building it: {e}"}
        yield {"type": "done"}
