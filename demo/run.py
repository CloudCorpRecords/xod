#!/usr/bin/env python3
"""
Films the "get me hired" demo: one command starts the practice careers site and a Xod of its own, with its own data
folder (demo/.data), so your own chats, profile and tasks are never in the shot and never touched.

  python3 demo/run.py              # practice site + Xod from this checkout, at http://127.0.0.1:8020
  python3 demo/run.py --app        # ... the installed Xod app instead (quit it first), for the real window and tray
  python3 demo/run.py --reset      # before a take: clears the profile, tasks, results, answers and what was received
  python3 demo/run.py --offline    # only the practice jobs, no real job boards (no internet needed)
  python3 demo/run.py --sample     # also gives the demo a made-up résumé (Sam Rivera) to find, instead of yours

The first run asks you to pick a model, like a new install; that choice stays in demo/.data for the next takes.
To find your own résumé in Google Drive, connect it in this Xod once (Tools → Connected apps → Google Drive).

The script (what to say, what to press) is in demo/SCRIPT.md.
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = HERE / ".data"
SITE_PORT, XOD_PORT = 8765, 8020
SITE = f"http://127.0.0.1:{SITE_PORT}"


def reset() -> None:
    for p in (DATA / "profile.json", DATA / "jobs" / "last.json"):
        p.unlink(missing_ok=True)
    for d in (DATA / "applications", DATA / "resumes", DATA / "demo-resume"):
        shutil.rmtree(d, ignore_errors=True)
    db = DATA / "xod.db"
    if db.exists():
        c = sqlite3.connect(db)
        for table in ("tasks", "conversations", "messages"):
            try:
                c.execute(f"DELETE FROM {table}")
            except sqlite3.Error:
                pass
        c.commit()
        c.close()
    for f in (HERE / "careers" / "received").glob("*.json"):
        f.unlink()
    print("Reset: profile, tasks, chats, job results, answers and received applications cleared (your model choice is kept).")


def sample_resume() -> None:
    """A made-up résumé in the demo's own Documents-like folder, for takes without your own. Sam's answers to the
    questions a résumé can't answer (Xod never guesses those) are in the profile already, as you'd have filled them once."""
    import docx
    import json
    prof = DATA / "profile.json"
    if not prof.exists():
        prof.write_text(json.dumps({"work_authorization": "US citizen, authorized to work in the United States",
                                    "needs_sponsorship": "no", "willing_to_relocate": "yes, within the US"}, indent=2))
    folder = DATA / "demo-resume"
    folder.mkdir(parents=True, exist_ok=True)
    d = docx.Document()
    for line in ["Sam Rivera", "Brooklyn, NY · sam.rivera@example.com · (555) 201-7788",
                 "linkedin.com/in/sam-rivera-dev · github.com/samrivera", "",
                 "Experience",
                 "Forward Deployed Engineer, Testco (2019 - present)",
                 "Deployed our data platform at 30 enterprise customers; wrote the Python and TypeScript integrations they ran on.",
                 "Built an LLM agent that drafted customer onboarding plans, cutting setup time from three weeks to four days.",
                 "Software Engineer, Brightline Logistics (2016 - 2019)",
                 "Built routing and warehouse tools in Python and SQL used by 200 operations staff.",
                 "", "Skills", "Python, TypeScript, SQL, Kubernetes, LLM agents, customer deployments"]:
        d.add_paragraph(line)
    path = folder / "Sam-Rivera-Resume.docx"
    d.save(str(path))
    print(f"Sample résumé: {path}")


def wait(url: str, secs: float = 60) -> bool:
    end = time.time() + secs
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> None:
    venv = ROOT / (".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python")
    try:
        import docx  # noqa: F401  (Xod's own Python has its packages; the system one may not)
    except ImportError:
        if venv.exists() and Path(sys.executable).resolve() != venv.resolve():
            os.execv(str(venv), [str(venv), __file__, *sys.argv[1:]])
        sys.exit("Run ./run.sh once first to set up Xod's Python, then try again.")
    DATA.mkdir(exist_ok=True)
    if "--reset" in sys.argv:
        reset()
        if len(sys.argv) == 2:
            return
    if "--sample" in sys.argv:
        sample_resume()
    env = {**os.environ, "XOD_DATA_DIR": str(DATA), "XOD_DEMO_BOARD": SITE, "XOD_NO_UPDATE_CHECK": "1", "PYTHONUTF8": "1"}
    if "--sample" in sys.argv:
        env["XOD_DEMO_RESUME_DIR"] = str(DATA / "demo-resume")
        env["XOD_DEMO_NAME"] = "Sam Rivera"          # and only that résumé is looked at: none of yours is in the shot
    if "--offline" in sys.argv:
        env["XOD_DEMO_ONLY"] = "1"
    site = subprocess.Popen([sys.executable, str(HERE / "careers" / "server.py"), str(SITE_PORT)])
    if not wait(SITE + "/api/jobs"):
        sys.exit("The practice careers site didn't start.")
    procs = [site]
    try:
        if "--app" in sys.argv:
            app = "/Applications/Xod.app/Contents/MacOS/Xod" if sys.platform == "darwin" else os.path.expandvars(r"%LOCALAPPDATA%\Programs\Xod\Xod.exe")
            if not Path(app).exists():
                sys.exit(f"Xod isn't installed at {app}.")
            procs.append(subprocess.Popen([app], env=env))
            print(f"Practice site: {SITE}/   received: {SITE}/received\nXod opened with the demo's own data. Press Ctrl+C here when you're done.")
        else:
            py = ROOT / (".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python")
            procs.append(subprocess.Popen([str(py if py.exists() else sys.executable), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                           "--port", str(XOD_PORT)], cwd=ROOT, env=env))
            if wait(f"http://127.0.0.1:{XOD_PORT}/", 120):
                print(f"\nXod (demo):    http://127.0.0.1:{XOD_PORT}/\nPractice site: {SITE}/\nReceived:      {SITE}/received\n"
                      "Say: \"find me forward deployed engineer jobs\". Ctrl+C here to stop.")
        procs[-1].wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
