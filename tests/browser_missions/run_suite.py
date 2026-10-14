"""
End-to-end tests for Xod's browser missions, against MockJobs (a fake job board
that runs locally on port 8799). Uses a fake identity, a separate "XodTest"
Keychain namespace, and a throwaway vault index, so your real logins are untouched.

Needs LM Studio running with the configured model. Each scenario takes ~10-15 min.

    .venv/bin/python tests/browser_missions/run_suite.py          # A, B and C
    .venv/bin/python tests/browser_missions/run_suite.py A        # just one

A: saved login + two-page form + resume upload + a question not in the profile + submit pause
B: a second application reuses the session cookie (no new login)
C: no account: sign up with a generated password saved to the Keychain, then apply
D: research across two shops (cookie banner) and report which is cheaper, with both prices
E: add a size to the cart (link opens a new tab, custom non-<select> dropdown)
F: send a message: must pause before "Send", and send exactly once after approval
Every run also checks that no password ever reached the model, logs, or UI history.
"""
import asyncio, json, os, re, subprocess, sys, tempfile, time, urllib.request, urllib.error, logging
HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="xod_mission_test_")
# Its own data folder, so its own browser (and browser port): never your real Xod's browser and its logins.
os.environ["XOD_DATA_DIR"] = os.environ.get("XOD_TEST_DATA_DIR") or os.path.join(TMP, "data")
os.environ["XOD_VAULT_PREFIX"] = "XodTest"
os.environ["XOD_VAULT_INDEX"] = os.path.join(TMP, "vault_test.json")
# Training examples from these fake sites go to a separate DB, never your real dataset.
os.environ["XOD_DATASET_DB"] = os.environ.get("XOD_DATASET_DB") or os.path.join(TMP, "test_examples.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
RESUME = os.path.join(TMP, "test_resume.pdf")
with open(RESUME, "wb") as f:
    f.write(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
import app.services.browser_sandbox as B
from app.services import credential_vault as V
from app.agent.researcher import AgenticResearcher

SITE = "http://localhost:8799"
PROFILE = {"full_name": "Test Person", "email": "test@example.com", "phone": "555-0100",
           "years_of_experience": "1-3", "work_authorization": "Yes, authorized to work in the US",
           "desired_salary": "90000", "resume_path": RESUME}
B.load_user_profile = lambda: dict(PROFILE)
B.SITE_NOTES_PATH = os.path.join(TMP, "site_notes.json")  # learned tips stay out of your real data
SB = B.browser_sandbox_service
SECRETS = []
EVENTS = []
LOG_CAPTURE = []

class Cap(logging.Handler):
    def emit(self, rec): LOG_CAPTURE.append(self.format(rec))
logging.getLogger().addHandler(Cap()); logging.getLogger().setLevel(logging.INFO)
_h = logging.StreamHandler(); _h.setLevel(logging.WARNING); logging.getLogger().addHandler(_h)

# Capture every LLM request body so we can prove passwords never reach the model.
from app.services import llm as L
_orig_cc = L.llm_service.chat_completion
async def spy_cc(*a, **kw):
    EVENTS.append(json.dumps(kw.get("messages", a[0] if a else ""), default=str))
    return await _orig_cc(*a, **kw)
L.llm_service.chat_completion = spy_cc

def free_port(port):
    """Ends whatever listens on the port (a mock from an earlier run); works on Windows, macOS and Linux."""
    import psutil
    for c in psutil.net_connections(kind="tcp"):
        if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid:
            try:
                psutil.Process(c.pid).kill()
            except psutil.Error:
                pass


def mock(seed=None):
    free_port(8799); time.sleep(0.5)
    args = [sys.executable, os.path.join(HERE, "mock_jobs.py"), "8799"] + (list(seed) if seed else [])
    p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); time.sleep(1.0)
    return p

def state():
    return json.loads(urllib.request.urlopen(SITE + "/admin/state").read())

async def turn(text):
    print(f"\n>>> USER: {text}", flush=True)
    t = time.time()
    async for ev in AgenticResearcher().run_research_stream([{"role": "user", "content": text}]):
        EVENTS.append(json.dumps(ev, default=str))
        if ev["type"] == "status" and ev["content"].startswith(("✓", "✗", "⏸", "•")):
            print("   " + ev["content"], flush=True)
        if ev["type"] == "content":
            print("   --- reply: " + ev["content"].split("<details>")[0].strip().replace("\n", " ")[:400], flush=True)
    m = SB.latest_mission or {}
    print(f"   [{time.time()-t:.0f}s] status={m.get('status')} steps={len(m.get('steps', []))} "
          f"thinking_steps={m.get('thinking_steps')} self_escalations={m.get('escalations')}", flush=True)
    return m

ALL_ACTIONS = []
async def conversation(first, replies, max_turns=7):
    ALL_ACTIONS.clear()
    m = await turn(first); ALL_ACTIONS.extend(s["action"] for s in m.get("steps", []))
    for _ in range(max_turns):
        st = m.get("status")
        if st == "needs_confirmation": m = await turn("go ahead")
        elif st == "needs_input": m = await turn(replies.get("needs_input", "continue"))
        else: return m
        ALL_ACTIONS.extend(s["action"] for s in m.get("steps", []))
    return m

async def main():
    await SB.restart_browser()
    which = sys.argv[1:] or ["A", "B", "C", "D", "E", "F"]
    timings = {}
    results = {}

    if "A" in which or "B" in which:
        pw = V.generate_password(); SECRETS.append(pw)
        p = mock(("test@example.com", pw))
        V.save_login(SITE, "test@example.com", pw, source="test")
        await SB.navigate(SITE + "/logout")
        if "A" in which:
            print("\n========== A: saved login, multi-page form, screening question, submit gate ==========")
            m = await conversation(f"Apply to the Junior Python Developer job on {SITE}",
                                   {"needs_input": "I don't have a security clearance, so answer No. Please continue."})
            st = state()
            results["A"] = {"final": m.get("status"), "submissions_for_job": sum(x["job"].startswith("Junior") for x in st["submissions"]),
                            "used_log_in": "log_in" in ALL_ACTIONS, "fields": st["submissions"][-1] if st["submissions"] else None}
        if "B" in which:
            print("\n========== B: session reuse (should not log in again) ==========")
            before = len(SB.action_history)
            m = await conversation(f"Apply to the Frontend Engineer job on {SITE}",
                                   {"needs_input": "No security clearance, answer No. continue"})
            results["B"] = {"final": m.get("status"), "submissions_for_job": sum(x["job"].startswith("Frontend") for x in state()["submissions"]),
                            "logged_in_again": "log_in" in ALL_ACTIONS}
        p.kill()

    if "C" in which:
        print("\n========== C: no account -> sign up with generated password ==========")
        V.delete_login(SITE)
        p = mock()
        await SB.navigate(SITE + "/logout")
        m = await conversation(f"Apply to the Senior Data Engineer job on {SITE}",
                               {"needs_input": "No security clearance, answer No. continue"})
        saved = V.get_login(SITE)
        if saved: SECRETS.append(saved[1])
        st = state()
        results["C"] = {"final": m.get("status"), "accounts_created": len(st["users"]), "saved_in_keychain": bool(saved),
                        "keychain_password_works_on_site": False, "used_create_password": "create_password" in ALL_ACTIONS,
                        "submissions_for_job": sum(x["job"].startswith("Senior") for x in st["submissions"])}
        if saved:
            # Prove the Keychain copy is the real password by logging in with it directly.
            import urllib.parse as up
            req = urllib.request.Request(SITE + "/login?next=/", data=up.urlencode({"email": saved[0], "password": saved[1]}).encode())
            class NoRedir(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **k): return None
            try: urllib.request.build_opener(NoRedir).open(req)
            except urllib.error.HTTPError as e: results["C"]["keychain_password_works_on_site"] = "err=1" not in (e.headers.get("Location") or "")
        p.kill()

    if any(x in which for x in ("D", "E", "F")):
        p = mock()
        if "D" in which:
            print("\n========== D: research across two shops ==========")
            t = time.time()
            m = await conversation(f"Compare the price of the Trail Runner 2 shoes on {SITE}/shopa and {SITE}/shopb and tell me which shop is cheaper", {})
            ans = (m.get("final_answer") or "") + " " + " ".join(m.get("notes", []))
            results["D"] = {"final": m.get("status"), "has_89.99": "89.99" in ans, "has_79.50": "79.5" in ans,
                            "says_shop_b_cheaper": bool(re.search(r"shop ?b[^.]{0,80}cheap|cheap[^.]{0,80}shop ?b", ans, re.I)),
                            "notes": m.get("notes"), "answer": (m.get("final_answer") or "")[:300]}
            timings["D"] = round(time.time() - t)
        if "E" in which:
            print("\n========== E: add to cart (new tab + custom dropdown) ==========")
            t = time.time()
            m = await conversation(f"Add the Trail Runner 2 in size 10 to my cart on {SITE}/shopb", {})
            cart = state()["cart"]
            results["E"] = {"final": m.get("status"), "cart": cart, "exactly_one_size_10": len(cart) == 1 and str(cart[0].get("size")) == "10"}
            timings["E"] = round(time.time() - t)
        if "F" in which:
            print("\n========== F: message must wait for approval ==========")
            t = time.time()
            m = await turn(f"Send a message to the Acme recruiter on {SITE}/messages saying: Hi Dana, I'm interested in the Junior Python Developer role.")
            before = len(state()["messages"])
            paused = m.get("status") == "needs_confirmation"
            if paused:
                m = await conversation("go ahead", {})
            msgs = state()["messages"]
            results["F"] = {"paused_first": paused, "sent_before_ok": before, "final": m.get("status"),
                            "messages_after": len(msgs), "text_ok": bool(msgs) and "interested" in msgs[-1].get("text", "")}
            timings["F"] = round(time.time() - t)
        p.kill()
    print("TIMINGS (s):", timings)

    blob = "\n".join(EVENTS) + "\n".join(LOG_CAPTURE) + json.dumps(SB.action_history, default=str)
    leaks = [i for i, s in enumerate(SECRETS) if s and s in blob]
    print("\n========== RESULTS ==========")
    print(json.dumps(results, indent=2, default=str))
    print("PASSWORD LEAK CHECK:", f"LEAKED (secrets {leaks})" if leaks else f"clean ({len(SECRETS)} secrets, {len(EVENTS)} events, {len(LOG_CAPTURE)} log lines scanned)")
    V.delete_login(SITE)

asyncio.run(main())
