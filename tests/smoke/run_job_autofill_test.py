"""
Your job profile, filled in from your résumé (services/job_autofill.py, agent/jobs.py):
  - whose résumé it is: yours by your name or email; someone else's is never used; several it can't tell apart: it asks
  - what's read from it: name, email, phone, links; a model's made-up email or link is dropped; only empty boxes are filled;
    work authorization and the like are never guessed
  - Google Drive (a stand-in for Google's Drive server): searched, someone else's Doc skipped, yours read and saved on this
    computer so applications can upload it
  - "just do all" in the chat: fills the profile, saves it and starts the applications that were waiting for it
No model, network or real Drive: test files in a throwaway folder.
  .venv/bin/python tests/smoke/run_job_autofill_test.py
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-autofill-"))
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
(TMP / "data").mkdir()

import docx  # noqa: E402

from app.agent import jobs  # noqa: E402
from app.services import job_autofill as af  # noqa: E402
from app.services.browser_sandbox import load_user_profile  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


def word(path: Path, lines):
    d = docx.Document()
    for line in lines:
        d.add_paragraph(line)
    d.save(str(path))
    return path


MINE = ["Sam Rivera", "Brooklyn, NY · sam.rivera@example.com · (555) 201-7788",
        "linkedin.com/in/sam-rivera-dev · github.com/samrivera",
        "Experience", "Forward Deployed Engineer, Testco (2019 - now)", "Built customer deployments in Python and TypeScript."]
THEIRS = ["Alex Medlin", "Austin, TX · alex.medlin@example.org · 555-300-1212", "Experience", "Product designer, 2016 - now"]
ME = {"names": ["Sam Rivera"], "emails": []}
af.identity = lambda: ME


def local(path: Path, mtime: float):
    os.utime(path, (mtime, mtime))
    return {"source": "local", "where": "this computer", "path": str(path), "name": path.name, "modified": mtime}


async def main():
    print("J1 whose résumé it is")
    check("yours: your full name at the top", af.whose("\n".join(MINE), ME) == "yours")
    check("yours: your email anywhere in it", af.whose("Samuel R.\nsam.rivera@example.com", {"names": [], "emails": ["sam.rivera@example.com"]}) == "yours")
    check("someone else's: another name at the top", af.whose("\n".join(THEIRS), ME) == "someone else")
    check("just a first name known: still matches yours", af.whose("\n".join(MINE), {"names": ["Sam"], "emails": []}) == "yours")
    check("nothing known about you: can't tell", af.whose("\n".join(THEIRS), {"names": [], "emails": []}) == "unknown")

    print("J2 what's read from it")
    f = af.plain_fields("\n".join(MINE))
    check("name, email and phone", f.get("full_name") == "Sam Rivera" and f.get("email") == "sam.rivera@example.com" and "201-7788" in f.get("phone", ""), str(f))
    check("LinkedIn and GitHub, as links", f.get("linkedin") == "https://www.linkedin.com/in/sam-rivera-dev" and f.get("github") == "https://github.com/samrivera", str(f))
    import time as _t
    check("years of experience from the dates (2019 - now)", f.get("years_of_experience") == str(_t.localtime().tm_year - 2019), str(f))
    check("…and none when the dates don't say", af.work_years("Engineer at Testco. Python.") == 0)
    kept = af._checked({"email": "sam@madeup.com", "linkedin": "https://linkedin.com/in/someone-else", "location": "Brooklyn, NY",
                        "years_of_experience": "about seven", "phone": "555-000-0000"}, "\n".join(MINE))
    check("a model's made-up email, link, phone and vague years are dropped; the city stays", kept == {"location": "Brooklyn, NY"}, str(kept))

    print("J3 picking yours from this computer")
    theirs = local(word(TMP / "Resume_AMedlin.docx", THEIRS), 2_000_000_000)        # newer, but someone else's
    mine = local(word(TMP / "Sam-Rivera-Resume.docx", MINE), 1_900_000_000)
    r = await af.autofill({"phone": "555-999-0000"}, files=[theirs, mine], use_model=False)
    check("it picks yours, not the newer one that's someone else's", r["resume"] and r["resume"]["name"] == "Sam-Rivera-Resume.docx", str(r.get("resume")))
    check("…and says it skipped theirs", r["skipped"] == ["Resume_AMedlin.docx"] and "someone else" in r["note"], r["note"])
    check("fills the empty boxes, with the résumé as the source", r["fields"].get("email") == "sam.rivera@example.com"
          and r["fields"].get("resume_path") == mine["path"] and "Sam-Rivera-Resume.docx" in r["from"]["email"], json.dumps(r["fields"]))
    check("…and leaves what you already typed alone", "phone" not in r["fields"])
    check("never guesses work authorization, sponsorship, salary or relocating",
          not {"work_authorization", "needs_sponsorship", "desired_salary", "willing_to_relocate"} & set(r["fields"]))

    print("J4 when it can't tell")
    a = local(word(TMP / "resume_a.docx", ["Robin Park", "robin@example.com"]), 1_800_000_000)
    b = local(word(TMP / "cv_b.docx", ["Jordan Lee", "jordan@example.com"]), 1_700_000_000)
    af.identity = lambda: {"names": [], "emails": []}
    r = await af.autofill({}, files=[a, b], use_model=False)
    check("two résumés and nothing known about you: it asks which, and fills nothing from either",
          r["resume"] is None and len(r["choices"]) == 2 and not r["fields"] and "which one is yours" in r["note"], r["note"])
    r = await af.autofill({}, files=[a], use_model=False)
    check("just one résumé: it's used", r["resume"] and r["fields"].get("email") == "robin@example.com", str(r.get("note")))
    af.identity = lambda: ME
    r = await af.autofill({}, files=[], use_model=False)
    check("no résumé anywhere: says so and how to connect Google Drive", r["resume"] is None and "Google Drive" in r["note"], r["note"])

    print("J5 Google Drive (stand-in server)")
    pdf = b"%PDF-1.4\n" + b"Sam Rivera resume " * 40
    calls = []

    async def fake_call(name, args, approved=False):
        calls.append((name, args))
        tool = name.split("__")[-1]
        if tool == "search_files":
            return {"success": True, "content": json.dumps({"files": [
                {"id": "doc1", "title": "Resume_AMedlin (1)", "mimeType": af.GDOC, "modifiedTime": "2026-10-16T17:08:54.407Z", "owner": "me@example.com"},
                {"id": "doc2", "title": "Sam Rivera Resume", "mimeType": af.GDOC, "modifiedTime": "2026-10-15T17:45:01.090Z", "owner": "me@example.com"},
                {"id": "img", "title": "resume photo.png", "mimeType": "image/png", "modifiedTime": "2026-10-16T00:00:00Z"}]})}
        if tool == "read_file_content":
            return {"success": True, "content": json.dumps({"content": "\n".join(THEIRS if args["fileId"] == "doc1" else MINE)})}
        if tool == "download_file_content":
            return {"success": True, "content": json.dumps({"content": base64.b64encode(pdf).decode()})}
        return {"success": False, "error": "no such tool"}

    from app.services import mcp_client
    mcp_client.mcp_hub.call = fake_call
    af.drive_server = lambda: "google-drive"
    files = await af.drive_files("google-drive")
    check("Drive is searched for résumés, and pictures are left out", [f["id"] for f in files] == ["doc1", "doc2"], str(files))
    r = await af.autofill({}, files=files, use_model=False)
    check("someone else's Doc is skipped, yours is used", r["resume"] and r["resume"]["name"] == "Sam Rivera Resume" and r["skipped"] == ["Resume_AMedlin (1)"], str(r.get("resume")))
    saved = r["fields"].get("resume_path", "")
    check("your Google Doc is saved on this computer as a PDF, for uploading", saved.endswith("Sam Rivera Resume.pdf") and Path(saved).read_bytes() == pdf
          and os.path.samefile(Path(saved).parent.parent, TMP / "data"), saved)     # samefile: Windows may spell TMP short (RUNNER~1)
    check("…asked for as a PDF", any(c[0].endswith("download_file_content") and c[1].get("exportMimeType") == "application/pdf" for c in calls))
    if sys.platform != "win32":     # on Windows the data folder is in the user's own AppData, which only they can read
        check("…and only private to you", oct(os.stat(saved).st_mode & 0o777) == "0o600", oct(os.stat(saved).st_mode))

    print("J6 \"just do all\" in the chat")

    async def no_model(text):
        return {}
    af.model_fields = no_model
    found = {"spec": {"role": "Forward Deployed Engineer"}, "jobs": [{"id": f"j{i}", "company": f"Co{i}", "title": "FDE", "url": f"https://example.com/{i}"} for i in range(1, 6)]}
    from app.services import job_hunt
    job_hunt.last = lambda: found
    turn = [{"role": "user", "content": "find me FDE jobs"}, {"role": "assistant", "content": "…" + jobs.MARK}, {"role": "user", "content": "x"}]
    for q in ("just do all", "do it all", "fill it in for me", "ok go ahead"):
        check(f"'{q}' after a jobs answer: do it all", (jobs.route(q, turn) or {}).get("mode") == "do_all", str(jobs.route(q, turn)))
    check("'apply to 2 and 4' is still picking those", jobs.route("apply to 2 and 4", turn) == {"mode": "apply"})
    check("'do it all' with no jobs answer before it isn't this", jobs.route("do it all", turn[:1] + [{"role": "user", "content": "x"}]) is None)
    check("'apply to all' picks them all (up to 10)", len(jobs.pick(found, "apply to all")) == 5)

    started = []
    async def fake_apply(chosen):
        return [started.append(j["id"]) or {"task_id": "t" + j["id"], "title": j["title"], "company": j["company"], "url": j["url"]} for j in chosen]
    jobs.apply = fake_apply
    jobs._pending[:] = ["j2", "j4"]
    mine2 = local(word(TMP / "Sam_Rivera_CV.docx", MINE), 1_950_000_000)

    async def find_mine():
        return [mine2]
    af.find = find_mine
    events = [e async for e in jobs.stream("just do all", turn, {"mode": "do_all"}, None)]
    p = load_user_profile()
    check("the profile is filled in and saved", p.get("full_name") == "Sam Rivera" and p.get("email") == "sam.rivera@example.com" and p.get("resume_path") == mine2["path"], json.dumps(p))
    check("…and the applications that were waiting start, only those", started == ["j2", "j4"], str(started))
    text = "".join(e.get("content", "") for e in events if e["type"] == "content")
    check("…saying what it filled and that each stops for your OK", "Started 2 applications" in text and "stops for your OK" in text, text[:300])
    check("…and nothing is waiting any more", jobs._pending == [])

    print("J7 asked to apply before the profile is ready: the form opens filled in")
    from app.services.browser_sandbox import PROFILE_PATH
    Path(PROFILE_PATH).unlink()
    events = [e async for e in jobs.stream("apply to 1 and 3", turn, {"mode": "apply"}, None)]
    need = next((e for e in events if e["type"] == "job_profile_needed"), {})
    check("the form gets your résumé's details to check", need.get("suggested", {}).get("email") == "sam.rivera@example.com" and need.get("ids") == ["j1", "j3"], json.dumps(need)[:300])
    text = "".join(e.get("content", "") for e in events if e["type"] == "content")
    check("…and the chat says you can just say \"do it all\"", "do it all" in text, text[:300])
    check("…nothing is saved until you say so", not Path(PROFILE_PATH).exists())

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
