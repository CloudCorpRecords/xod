"""
Fills in your job profile from what's already yours, so you don't type it: your résumé, wherever it is, and what Xod
already knows about you.

  find()      résumés from every place Xod can look: this computer (Spotlight on a Mac, the usual folders on Windows), the
              Google Drive for desktop folder, and Google Drive itself when it's connected (Tools → Connected apps)
  autofill()  picks the one that's yours, reads it, and pulls out your name, email, phone, links, city, years of experience
              and a few lines about you; fills only the boxes that are empty. A Drive file is downloaded into Xod's data
              folder so applications can upload it.

Whose résumé: a Drive or a Downloads folder often holds other people's (a friend's to review, a candidate's). One whose
name or email is yours wins; one that plainly names someone else is never used; when it can't tell between several,
it asks. Answers that are yours to give (work authorization, sponsorship, salary, relocating) are never guessed.
"""
import base64
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("xod.job_autofill")

NAME_RE = re.compile(r"(resume|résumé|\bcv\b|_cv|cv_|-cv|cv-|curriculum)", re.I)
DOC_EXT = (".pdf", ".docx", ".doc")
# What a résumé can tell; the rest of the profile is the person's to answer.
FROM_RESUME = ("full_name", "email", "phone", "location", "linkedin", "github", "portfolio", "years_of_experience", "short_bio")
DRIVE_QUERY = ("(title contains 'resume' or title contains 'résumé' or title contains 'cv' or title contains 'curriculum') "
               "and trashed = false")
GDOC = "application/vnd.google-apps.document"


# ---------------------------------------------------------------- who you are

def identity() -> Dict[str, List[str]]:
    """Names and emails Xod already knows are yours: your profile, what you told it, this computer's account."""
    if os.environ.get("XOD_DEMO_NAME"):           # filming with a made-up résumé (demo/run.py --sample): that person
        return {"names": [os.environ["XOD_DEMO_NAME"]], "emails": []}
    names, emails = [], []
    try:
        from app.services.browser_sandbox import load_user_profile
        p = load_user_profile()
        names.append(p.get("full_name") or "")
        emails.append(p.get("email") or "")
    except Exception:
        pass
    try:
        from app.services import user_memory
        for f in user_memory.facts():
            t = f.get("text") or ""
            if f.get("key") == "name":
                names.append(re.sub(r"(?i)^\s*name is\s+", "", t).strip(" ."))
            elif f.get("key") == "email":
                emails.append(re.sub(r"(?i)^\s*email is\s+", "", t).strip(" ."))
    except Exception:
        pass
    try:
        from app.services.user_memory import _from_account
        for f in _from_account():
            names.append(f["fact"].replace("Name is ", "", 1))
    except Exception:
        pass
    clean = lambda xs: list(dict.fromkeys(x.strip() for x in xs if x and x.strip()))
    return {"names": clean(names), "emails": [e.lower() for e in clean(emails) if "@" in e]}


def whose(text: str, who: Dict[str, List[str]]) -> str:
    """"yours", "someone else" or "unknown", from the top of the résumé (where the name is) and any email in it."""
    top = (text or "")[:600]
    low = text.lower() if text else ""
    if any(e in low for e in who.get("emails", [])):
        return "yours"
    words = {w.lower() for n in who.get("names", []) for w in re.findall(r"[^\W\d_]{2,}", n)}
    full = [n.lower() for n in who.get("names", []) if " " in n.strip()]
    head = top.lower()
    if any(f in head for f in full):
        return "yours"
    shown = _name_line(top)
    if shown:
        got = {w.lower() for w in re.findall(r"[^\W\d_]{2,}", shown)}
        if words and got & words and (len(got & words) >= 2 or not full):
            return "yours"
        if words:
            return "someone else"
    return "unknown"


def _name_line(top: str) -> str:
    """The person's name on a résumé is its first short line of two to four capitalized words."""
    for line in top.splitlines()[:6]:
        line = re.sub(r"^-+ Page \d+ of \d+ -+$", "", line.strip())
        if not line:
            continue
        words = line.split()
        if 2 <= len(words) <= 4 and all(w[:1].isupper() for w in words) and not re.search(r"[@\d|/:]", line):
            return line
    return ""


# ---------------------------------------------------------------- finding résumés

def local_files(limit: int = 12) -> List[Dict[str, Any]]:
    """Résumé / CV files on this computer, newest first."""
    found: List[Dict[str, Any]] = []
    if os.environ.get("XOD_DEMO_RESUME_DIR"):      # filming with a made-up résumé (demo/run.py --sample): only that folder
        _walk(found, [Path(os.environ["XOD_DEMO_RESUME_DIR"])], "Documents", 2)
        return found
    if sys.platform == "darwin":
        import subprocess
        q = ('(kMDItemFSName == "*resume*"cd || kMDItemFSName == "*résumé*"cd || kMDItemFSName == "*cv*"cd || kMDItemFSName == "*curriculum*"cd) && '
             '(kMDItemContentTypeTree == "com.adobe.pdf" || kMDItemFSName == "*.docx"cd || kMDItemFSName == "*.doc"cd)')
        try:
            out = subprocess.run(["mdfind", "-onlyin", str(Path.home()), q], capture_output=True, text=True, timeout=8).stdout
        except Exception:
            out = ""
        for line in out.splitlines():
            if "/Library/" in line or "/." in line:
                continue
            _add(found, line, "this computer")
    else:
        _walk(found, [Path.home() / d for d in ("Documents", "Desktop", "Downloads", "OneDrive")], "this computer", 6)
    _walk(found, drive_folders(), "Google Drive (on this computer)", 6)
    found.sort(key=lambda f: -f["modified"])
    return found[:limit]


def drive_folders() -> List[Path]:
    """Where Google Drive for desktop keeps your Drive, if it's installed."""
    out: List[Path] = []
    if sys.platform == "darwin":
        cs = Path.home() / "Library" / "CloudStorage"
        try:
            out += [d / "My Drive" for d in cs.iterdir() if d.name.startswith("GoogleDrive") and (d / "My Drive").is_dir()]
        except OSError:
            pass
    elif sys.platform == "win32":
        import string
        out += [Path(f"{c}:/My Drive") for c in string.ascii_uppercase[3:] if Path(f"{c}:/My Drive").is_dir()]
    out += [p for p in (Path.home() / "Google Drive" / "My Drive", Path.home() / "My Drive") if p.is_dir()]
    return out


def _add(found: List[Dict[str, Any]], path: str, where: str) -> None:
    try:
        st = os.stat(path)
    except OSError:
        return
    if not any(f["path"] == path for f in found):
        found.append({"source": "local", "where": where, "path": path, "name": os.path.basename(path), "modified": st.st_mtime})


def _walk(found: List[Dict[str, Any]], roots: List[Path], where: str, budget: float) -> None:
    t0 = time.time()
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d.lower() not in ("node_modules", "appdata")]
            for n in filenames:
                if n.lower().endswith(DOC_EXT) and NAME_RE.search(n):
                    _add(found, os.path.join(dirpath, n), where)
            if time.time() - t0 > budget:
                return


def drive_server() -> Optional[str]:
    """The connected server that is Google Drive (the one-click connector, or one pasted in by hand)."""
    from app.services.mcp_client import mcp_hub
    for s in mcp_hub.status():
        tools = {t["name"] for t in s.get("tools") or []}
        if s.get("connected") and {"search_files", "read_file_content"} <= tools and (
                s.get("connector") == "google-drive" or "drive" in s["name"].lower() or "drivemcp" in str(s.get("command") or "")):
            return s["name"]
    return None


def _payload(res: Dict[str, Any]) -> Any:
    if isinstance(res.get("data"), dict):
        return res["data"]
    try:
        return json.loads(res.get("content") or "")
    except ValueError:
        return {}


async def drive_files(server: str, limit: int = 10) -> List[Dict[str, Any]]:
    from app.services.mcp_client import mcp_hub
    res = await mcp_hub.call(f"mcp__{server}__search_files", {"query": DRIVE_QUERY, "pageSize": limit, "excludeContentSnippets": True})
    if not res.get("success"):
        logger.info(f"Drive search didn't run: {res.get('error') or res.get('message')}")
        return []
    out = []
    for f in (_payload(res) or {}).get("files") or []:
        mime = f.get("mimeType") or ""
        if mime != GDOC and not (f.get("title") or "").lower().endswith(DOC_EXT) and "pdf" not in mime and "word" not in mime:
            continue
        try:
            mod = time.mktime(time.strptime((f.get("modifiedTime") or "")[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        except ValueError:
            mod = 0
        out.append({"source": "drive", "where": "Google Drive", "id": f.get("id"), "name": f.get("title") or "résumé",
                    "mime": mime, "owner": f.get("owner"), "modified": mod, "url": f.get("viewUrl")})
    return out


async def find() -> List[Dict[str, Any]]:
    import asyncio
    files = await asyncio.to_thread(local_files)
    server = None if os.environ.get("XOD_DEMO_RESUME_DIR") else drive_server()
    if server:
        files += await drive_files(server)
    files.sort(key=lambda f: -f["modified"])
    return files


# ---------------------------------------------------------------- reading one

async def read(f: Dict[str, Any]) -> str:
    if f["source"] == "local":
        import asyncio
        from app.services.document_parser import document_parser_service
        r = await asyncio.to_thread(document_parser_service.parse_file, f["path"], 20000)
        return "" if r.get("error") else (r.get("content") or "")
    from app.services.mcp_client import mcp_hub
    res = await mcp_hub.call(f"mcp__{drive_server()}__read_file_content", {"fileId": f["id"]})
    if not res.get("success"):
        return ""
    p = _payload(res)
    return (p.get("content") or p.get("text") or "") if isinstance(p, dict) and p else (res.get("content") or "")


async def download(f: Dict[str, Any]) -> Optional[str]:
    """A Drive résumé saved into Xod's data folder (a Google Doc as a PDF), so an application can upload it."""
    from app.paths import DATA_DIR
    from app.services.mcp_client import mcp_hub
    args = {"fileId": f["id"]}
    if f.get("mime") == GDOC:
        args["exportMimeType"] = "application/pdf"
    res = await mcp_hub.call(f"mcp__{drive_server()}__download_file_content", args)
    if not res.get("success"):
        return None
    p = _payload(res)
    blob = ""
    if isinstance(p, dict):
        blob = p.get("content") or p.get("data") or p.get("base64") or ""
    if not blob:     # whatever long base64 string the server sent
        blob = max(re.findall(r"[A-Za-z0-9+/=_-]{200,}", res.get("content") or "") or [""], key=len)
    try:
        raw = base64.b64decode(blob + "=" * (-len(blob) % 4), altchars=b"-_" if "-" in blob or "_" in blob else None)
    except Exception:
        return None
    if len(raw) < 200:
        return None
    name = re.sub(r"[^\w .()-]", "_", f["name"]).strip() or "Resume"
    ext = ".pdf" if f.get("mime") == GDOC or raw[:4] == b"%PDF" else Path(name).suffix or ".docx"
    folder = Path(DATA_DIR) / "resumes"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / (Path(name).stem + ext)
    out.write_bytes(raw)
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass
    return str(out)


# ---------------------------------------------------------------- the fields

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")


def plain_fields(text: str) -> Dict[str, str]:
    """What plain patterns can find, with no model: contact details and links."""
    out: Dict[str, str] = {}
    m = EMAIL_RE.search(text)
    if m:
        out["email"] = m.group(0).rstrip(".")
    m = PHONE_RE.search(text)
    if m:
        out["phone"] = m.group(0).strip()
    m = re.search(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w%-]+/?", text, re.I)
    if m:
        out["linkedin"] = "https://" + re.sub(r"^(https?://)?(www\.)?", "www.", m.group(0))
    m = re.search(r"(?:https?://)?(?:www\.)?github\.com/[\w-]+/?(?=[\s,|)]|$)", text, re.I)
    if m:
        out["github"] = "https://" + re.sub(r"^(https?://)?(www\.)?", "", m.group(0))
    name = _name_line(text[:600])
    if name:
        out["full_name"] = name
    years = work_years(text)
    if years:
        out["years_of_experience"] = str(years)
    return out


def work_years(text: str) -> int:
    """Years since the earliest job that runs to now ("2018 - present"), from the dates in the résumé, not a guess."""
    now = time.localtime().tm_year
    starts = [int(a) for a, b in re.findall(r"\b((?:19|20)\d{2})\s*(?:[-–—]|to)\s*(present|now|current|today|(?:19|20)\d{2})\b", text, re.I)
              if 1960 < int(a) <= now]
    return now - min(starts) if starts and 0 < now - min(starts) < 60 else 0


PROMPT = """From this résumé, return ONLY a JSON object with these keys (empty string when the résumé doesn't say):
full_name, email, phone, location (city and state or country), linkedin, github, portfolio (personal site, not LinkedIn
or GitHub), years_of_experience (a number, from the work history), short_bio (2-3 sentences in first person about what
they do and what they're good at, only from the résumé).

Résumé:
"""


async def model_fields(text: str) -> Dict[str, str]:
    from app.services import usage
    tag = usage.purpose.set("job_profile")     # the Usage tab: "Job profile" (no task tag: a résumé isn't training data)
    try:
        from app.services.llm import llm_service, parse_llm_json
        msg = await llm_service.chat_completion([{"role": "user", "content": PROMPT + text[:9000]}], temperature=0.1,
                                                max_tokens=700, think=False)
        d = parse_llm_json(msg.get("content") or "")
    except Exception as e:
        logger.info(f"no model for the résumé: {e}")
        return {}
    finally:
        usage.purpose.reset(tag)
    return {k: str(d.get(k) or "").strip()[:1500] for k in FROM_RESUME if str(d.get(k) or "").strip()}


def _checked(fields: Dict[str, str], text: str) -> Dict[str, str]:
    """Keeps what the résumé backs up: a model can make up an email or a link; it can't make one appear in the text."""
    low = text.lower()
    out = {}
    for k, v in fields.items():
        if k in ("email", "linkedin", "github", "portfolio"):
            core = re.sub(r"^(https?://)?(www\.)?", "", v.lower()).rstrip("/")
            if core and core not in low:
                continue
        if k == "phone" and re.sub(r"\D", "", v)[-7:] not in re.sub(r"\D", "", text):
            continue
        if k == "years_of_experience" and not re.fullmatch(r"\d{1,2}(\.\d)?\+?", v):
            continue
        out[k] = v
    return out


# ---------------------------------------------------------------- putting it together

async def autofill(current: Optional[Dict[str, Any]] = None, files: Optional[List[Dict[str, Any]]] = None,
                   use_model: bool = True) -> Dict[str, Any]:
    """Suggestions for the empty fields of `current` (the profile as it is now). Saves nothing.

    {"fields": {...}, "from": {field: where it came from}, "resume": the chosen file or None,
     "choices": [files] when it couldn't tell which résumé is yours, "skipped": [someone else's], "note": one line}"""
    current = current or {}
    who = identity()
    files = await find() if files is None else files
    seen, skipped, unsure = [], [], []
    chosen, text = None, ""
    for f in files[:8]:
        t = await read(f)
        if not t.strip() or t.startswith("[Scanned"):
            continue
        w = whose(t, who)
        f = {**f, "whose": w}
        seen.append(f)
        if w == "yours":
            chosen, text = f, t
            break
        (skipped if w == "someone else" else unsure).append((f, t))
    if not chosen and len(unsure) == 1 and not skipped:
        chosen, text = unsure[0]
    out: Dict[str, Any] = {"fields": {}, "from": {}, "resume": None, "choices": [], "skipped": [s[0]["name"] for s in skipped]}
    if not chosen:
        out["choices"] = [{k: v for k, v in f.items() if k != "whose"} for f, _ in unsure][:6]
        if not files:
            out["note"] = ("I didn't find a résumé on this computer" + (" or in Google Drive" if drive_server() else "")
                           + ". Pick the file, or connect Google Drive in Tools → Connected apps so I can look there.")
        elif out["choices"]:
            out["note"] = "I found a few résumés and couldn't tell which one is yours. Pick it and I'll fill in the rest."
        else:
            out["note"] = "The résumés I found are someone else's (" + ", ".join(out["skipped"][:3]) + "). Pick yours."
        _known(out, current, who)
        return out

    found = plain_fields(text)      # contact details, links and years from the text itself win over the model's reading
    if use_model:
        found = {**{k: v for k, v in _checked(await model_fields(text), text).items() if v}, **found}
    label = f"your résumé ({chosen['name']}" + (", Google Drive)" if chosen["source"] == "drive" else ")")
    for k, v in found.items():
        if v and not str(current.get(k) or "").strip():
            out["fields"][k] = v
            out["from"][k] = label
    path = chosen.get("path")
    resume_ok = current.get("resume_path") and os.path.isfile(os.path.expanduser(str(current["resume_path"])))
    if not resume_ok:
        if chosen["source"] == "drive":
            path = await download(chosen)
        if path:
            out["fields"]["resume_path"] = path
            out["from"]["resume_path"] = "Google Drive (saved a copy on this computer)" if chosen["source"] == "drive" else chosen["where"]
    out["resume"] = {k: chosen.get(k) for k in ("source", "where", "name", "path", "url", "whose")}
    _known(out, current, who)
    n = len(out["fields"])
    out["note"] = (f"Filled in {n} thing{'s' if n != 1 else ''} from {label}" if n else f"Your profile already has everything in {label}") + (
        f". Skipped {len(skipped)} that {'is' if len(skipped) == 1 else 'are'} someone else's." if skipped else ".")
    return out


def _known(out: Dict[str, Any], current: Dict[str, Any], who: Dict[str, List[str]]) -> None:
    """Your name and email from what Xod already knows, when the résumé didn't fill them."""
    full = next((n for n in who["names"] if " " in n), "")
    for k, v in (("full_name", full), ("email", (who["emails"] or [""])[0])):
        if v and k not in out["fields"] and not str(current.get(k) or "").strip():
            out["fields"][k] = v
            out["from"][k] = "what Xod knows about you"
