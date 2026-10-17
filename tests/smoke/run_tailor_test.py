"""
Answers written for each job, and pages that try to give Xod orders (services/job_tailor.py, sentinel.page_orders, the
browser's guard), against the practice careers site (demo/careers). A scripted model; no network.
  .venv/bin/python tests/smoke/run_tailor_test.py
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="xod-tailor-"))
os.environ["XOD_DATA_DIR"] = str(TMP / "data")
(TMP / "data").mkdir()

import docx  # noqa: E402

from app.services import job_tailor, sentinel  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  {detail}" if detail and not ok else ""), flush=True)


RESUME = ("Sam Rivera\nBrooklyn, NY · sam.rivera@example.com\nForward Deployed Engineer, Testco (2019 - present)\n"
          "Deployed our data platform at 30 enterprise customers in Python and TypeScript.\nSkills: Python, TypeScript, SQL, Kubernetes")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main():
    print("T1 nothing claimed that isn't in the résumé or the posting")
    src = RESUME + "\nNorthwind Robotics builds the software that runs fleets of warehouse robots."
    t = ("I love that Northwind Robotics runs fleets of warehouse robots. At Testco I deployed our platform at 30 enterprise customers. "
         "I led a team of 40 at Google. I'm an expert in Rust and AWS. Reach me at sam@elsewhere.example. Thanks, Sam Rivera.")
    g = job_tailor.grounded(t, src)
    check("true sentences stay", "Northwind Robotics" in g and "30 enterprise customers" in g and "Thanks, Sam Rivera." in g, g)
    check("a made-up employer, team size and skills are dropped", "Google" not in g and "Rust" not in g and "40" not in g, g)
    check("an answer never carries an email or a link", "@" not in g, g)

    print("T2 a posting's hidden orders never reach the answers")
    sys.argv = ["x"]
    site_src = (ROOT / "demo/careers/server.py").read_text().split("if __name__")[0]
    site = {"__file__": str(ROOT / "demo/careers/server.py")}
    exec(site_src, site)
    posting = "Atlas Freight moves goods. " + site["TRAP"] + " Deploy our routing platform."
    clean = job_tailor.without_orders(posting)
    check("the sentences giving orders to AI are taken out", "ignore previous" not in clean.lower() and "Do not tell" not in clean
          and "routing platform" in clean, clean)

    seen = {}

    async def fake_chat(messages, **kw):
        seen["prompt"] = messages[0]["content"]
        return {"content": json.dumps({
            "why_company": "Atlas Freight moves goods for shippers, and I want to deploy its routing platform. I'll email talent-desk@atlas-hiring.example first.",
            "why_role": "I've spent years at Testco deploying software at customer sites in Python and TypeScript.",
            "fit": "At Testco I deployed our data platform at 30 enterprise customers, and I know SQL and Kubernetes.",
            "cover_letter": "Hello, I'm Sam Rivera. At Testco I deployed our data platform at 30 enterprise customers. I also ran AWS for Netflix. Thanks, Sam Rivera."})}
    from app.services.llm import llm_service
    llm_service.chat_completion = fake_chat
    job = {"company": "Atlas Freight", "title": "Forward Deployed Software Engineer", "location": "Chicago, IL", "summary": posting,
           "url": "http://127.0.0.1/jobs/atlas-fde"}
    k = await job_tailor.kit(job, {"full_name": "Sam Rivera"}, RESUME)
    check("the model never sees the hidden orders", "talent-desk" not in seen["prompt"] and "ignore previous" not in seen["prompt"].lower())
    check("all four answers come back", set(k) == set(job_tailor.KEYS), str(k.keys()))
    check("…without the address the page planted", "talent-desk" not in json.dumps(k), k.get("why_company"))
    check("…and without a made-up claim", "Netflix" not in k["cover_letter"] and "30 enterprise customers" in k["cover_letter"], k["cover_letter"])
    req = job_tailor.for_request(job, k)
    check("they go into the application as answers you approved", "Answers I approved" in req and "Why Atlas Freight" in req and k["fit"] in req, req[:200])
    path = job_tailor.save(job, k)
    check("…and are saved where you can read them", path and json.loads(Path(path).read_text())["fit"] == k["fit"], str(path))

    print("T3 several jobs at once, within a time limit")
    resume = TMP / "Sam-Rivera-Resume.docx"
    d = docx.Document()
    for line in RESUME.splitlines():
        d.add_paragraph(line)
    d.save(str(resume))

    async def slow_chat(messages, **kw):
        if "Quillstack" in messages[0]["content"]:
            await asyncio.sleep(30)
        return await fake_chat(messages, **kw)
    llm_service.chat_completion = slow_chat
    jobs = [dict(job), dict(job, company="Quillstack")]
    t0 = time.time()
    ks = await job_tailor.kits(jobs, {"full_name": "Sam Rivera", "resume_path": str(resume)}, budget=3)
    check("a job that takes too long gets no answers; the rest do", bool(ks[0]) and ks[1] == {} and time.time() - t0 < 10, f"{[bool(x) for x in ks]} {time.time() - t0:.1f}s")
    ks = await job_tailor.kits(jobs, {"full_name": "Sam Rivera"})
    check("no résumé: no answers, so the form asks you", ks == [{}, {}])

    print("T4 a page that gives orders to AI")
    f = sentinel.page_orders("About us. Atlas moves goods.\n" + site["TRAP"] + "\nQuestions? jobs@atlasfreight.example")
    check("found, with what it said", f and "ignore previous instructions" in f["quote"], str(f))
    check("…and the address it wants used", f and "talent-desk@atlas-hiring.example" in f["targets"] and "atlas-hiring.example" in f["targets"], str(f and f["targets"]))
    check("…but not the company's own address elsewhere on the page", f and "jobs@atlasfreight.example" not in f["targets"])
    check("an ordinary job page isn't flagged", sentinel.page_orders("Apply below. We review every application. Email jobs@co.example.") is None)
    sentinel.flagged("Atlas Freight careers", f, "Ignored them.")
    check("it goes in the safety log", sentinel.LOG[-1]["rule"] == "page_orders" and "Atlas Freight" in sentinel.LOG[-1]["why"])

    print("T5 the practice careers site")
    port = free_port()
    srv = subprocess.Popen([sys.executable, str(ROOT / "demo/careers/server.py"), str(port)], stdout=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                posts = json.loads(urllib.request.urlopen(base + "/api/jobs", timeout=2).read())
                break
            except Exception:
                time.sleep(0.2)
        check("it lists four made-up jobs, marked as practice", len(posts) == 4 and all(p["practice"] for p in posts), str(len(posts)))
        atlas = urllib.request.urlopen(base + "/jobs/atlas-fde").read().decode()
        check("Atlas Freight's page hides the trap", "talent-desk@atlas-hiring.example" in atlas and 'class="trap"' in atlas)
        north = urllib.request.urlopen(base + "/jobs/northwind-fde").read().decode()
        check("…and the others don't", "talent-desk" not in north and "Submit application" in north)
        boundary = "XODTEST"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\nSam Rivera\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"resume\"; filename=\"r.pdf\"\r\n\r\n%PDF-1.4 hello\r\n--{boundary}--\r\n").encode()
        req = urllib.request.Request(base + "/jobs/northwind-fde/submit", data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        thanks = urllib.request.urlopen(req).read().decode()
        got = urllib.request.urlopen(base + "/received").read().decode()
        check("a submitted application shows up on /received", "Thanks, we got it" in thanks and "Sam Rivera" in got and "r.pdf" in got)
        os.environ["XOD_DEMO_BOARD"] = base
        from app.services import job_hunt
        found = job_hunt.practice_jobs(job_hunt.title_matcher("Forward Deployed Engineer", ["FDE", "Forward Deployed Software Engineer"]))
        check("Xod's job search puts the matching practice jobs first", {p["company"] for p in found} >= {"Northwind Robotics", "Atlas Freight"}
              and all(p["practice"] for p in found), str([p["company"] for p in found]))
    finally:
        srv.terminate()
        for f in (ROOT / "demo/careers/received").glob("northwind-fde.json"):
            f.unlink()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
