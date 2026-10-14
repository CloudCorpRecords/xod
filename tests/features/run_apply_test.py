"""
Applying for a job, end to end, against a pretend careers page on this Mac (tests/fixtures/jobs):
the task opens the posting, fills the form from a test job profile, uploads a test résumé, answers
the questions, and stops for approval before submitting; after approval, it submits.

No real company site is touched. Uses a throwaway data folder, test profile values, the model that's
loaded, and Xod's headless browser (its own port, see browser_sandbox._debug_port):
  .venv/bin/python tests/features/run_apply_test.py
"""
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
TMP = Path(tempfile.mkdtemp(prefix="xod-apply-"))
SUBMITTED = TMP / "submitted.json"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not ok else ""), flush=True)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def tiny_pdf(path: Path, text: str) -> None:
    body = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    path.write_bytes(bytes(out))


def wait_task(base, tid, statuses, limit=900):
    end = time.time() + limit
    t = {}
    while time.time() < end:
        tasks = call(base, "GET", "/api/tasks").get("tasks", [])
        t = next((x for x in tasks if x["id"] == tid), {})
        if t.get("status") in statuses:
            return t
        time.sleep(4)
    return t


def main():
    data = TMP / "data"
    data.mkdir()
    resume = TMP / "Test_Applicant_Resume.pdf"
    tiny_pdf(resume, "Test Applicant - Software engineer - Python, TypeScript, customer deployments")
    (data / "profile.json").write_text(json.dumps({
        "full_name": "Test Applicant", "email": "test.applicant@example.com", "phone": "555-0100",
        "linkedin": "https://www.linkedin.com/in/test-applicant", "location": "New York, NY", "resume_path": str(resume),
        "work_authorization": "US citizen, authorized to work in the United States", "needs_sponsorship": "no",
        "short_bio": "Software engineer who likes working with customers; built deployment tooling at two startups."}))
    site_port, port = free_port(), free_port()
    site = subprocess.Popen([sys.executable, str(ROOT / "tests/fixtures/jobs/server.py"), str(site_port), str(SUBMITTED)])
    env = {**os.environ, "XOD_DATA_DIR": str(data), "XOD_NO_UPDATE_CHECK": "1"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                              cwd=ROOT, env=env, stdout=open(TMP / "server.log", "w"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/api/tasks", timeout=2)
                break
            except Exception:
                time.sleep(0.25)
        st = call(base, "GET", "/api/jobs/profile")["status"]
        check("the test job profile is ready", st["ready"], json.dumps(st))
        # Exactly what the jobs card's Apply button creates (agent/jobs.apply).
        sys.path.insert(0, str(ROOT))
        url = f"http://127.0.0.1:{site_port}/jobs/fde"
        req = (f"Apply to the Forward Deployed Engineer role at Testco: {url} . Fill the application with my profile and upload my "
               f"résumé, answer questions from my profile, and stop for my OK before submitting.")
        t = call(base, "POST", "/api/tasks", {"request": req})["task"]
        print("   task", t["id"], flush=True)
        t = wait_task(base, t["id"], ("waiting", "done", "failed"))
        print("   status:", t.get("status"), "|", t.get("waiting_kind"), "|", (t.get("now") or "")[:160], flush=True)
        for h in (t.get("history") or [])[-12:]:
            print("    ·", str(h.get("text"))[:160], flush=True)
        check("it stops for approval before submitting", t.get("status") == "waiting" and t.get("waiting_kind") == "approval" and not SUBMITTED.exists(),
              f"{t.get('status')} / {t.get('waiting_kind')}")
        call(base, "POST", f"/api/tasks/{t['id']}/approve")
        t = wait_task(base, t["id"], ("done", "failed", "waiting"), 600)
        got = json.loads(SUBMITTED.read_text()) if SUBMITTED.exists() else {}
        print("   submitted:", json.dumps(got)[:400], flush=True)
        check("after approval it submits", bool(got), t.get("status"))
        check("name and email from the profile", got.get("name") == "Test Applicant" and got.get("email") == "test.applicant@example.com", json.dumps(got)[:200])
        check("the résumé was uploaded", isinstance(got.get("resume"), dict) and got["resume"].get("bytes", 0) > 100, json.dumps(got.get("resume")))
        check("work authorization answered", got.get("auth") == "Yes", str(got.get("auth")))
        check("the 'why' question has a real answer", len(str(got.get("why") or "")) > 40, str(got.get("why"))[:200])
    finally:
        server.terminate()
        site.terminate()
        import psutil                      # the test Xod's browser (its profile is in the test data folder); any system
        for p in psutil.process_iter(["cmdline"]):
            try:
                if any(str(data) in c for c in (p.info["cmdline"] or [])):
                    p.kill()
            except psutil.Error:
                pass
    failed = [n for n, ok in results if not ok]
    print(f"\nSUMMARY {'PASS' if not failed else 'FAIL'} {failed}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
