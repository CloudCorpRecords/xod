"""Grade fix cluster 3 (Tier 4 discovery + adjudication), from the
2026-11-13 live grade (LIVE-GRADES-2026-11-13.md, Tier 4 live):

(a) T4-L1 died at step 1 because the AGE-39 discovery judgment fired
on a classify-all-sources step ("identify the registry extract,
company blog, and any wire reprint") and demanded a single
matching-file statement. The judgment layer now separates the two
shapes: an explicit single-target criterion is a discovery; a
multi-source classification step is judged by the classification
standard (each candidate named with what it is: kind, independence,
provenance). A bare listing classifies nothing and fails with that
true reason.

(b) T4-L2 reported secondary-source counts its own text flagged as
not verified against NASA or the IAU, with primary-source ranking
absent. Where a step's own instruction demands primary-over-secondary
ranking, the sourced-answer gate now enforces it deterministically:
a report that flags its answer unverified against the primary
authority, or names no primary source at all, is not the answer its
instruction required. The planner prompt also carries the ranking
rule (primary over secondary, a reprint is not an independent
confirmation, never present a secondary-only figure as established).

Run: .venv/bin/python tests/brain/run_tier4_classification_ranking_test.py
"""
import asyncio, json, os, sys, tempfile
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "tests.support.filekeyring.FileKeyring")
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="xod_t4_classrank_"))
os.environ.update({"HOME": str(TMP), "XOD_DATA_DIR": str(TMP / "data"),
                   "XOD_NO_UPDATE_CHECK": "1"})
(TMP / "data").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

import app.services.missions as M  # noqa: E402
import app.services.task_queue as TQ  # noqa: E402

DBF = TMP / "xod.db"
M.DB = DBF
TQ.DB_PATH, TQ.SHOTS_DIR = DBF, TMP / "shots"
TQ.notify = lambda title, msg: None
if hasattr(TQ, "_phone"):
    TQ._phone = lambda task, kind: None
M._spawn = lambda mid: None

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)

S = TMP / "xod-t4-scratch"
(S / "sources").mkdir(parents=True, exist_ok=True)
for name, body in [("registry_primary.txt", "REGISTRY EXTRACT: Acme incorporated 2018-03-14.\n"),
                   ("launch_blog.txt", "COMPANY BLOG: Acme launched 2019-06-01.\n"),
                   ("syndicated_wire.txt", "WIRE REPRINT of the company blog.\n")]:
    (S / "sources" / name).write_text(body)

CLASS_SUB = {"title": "Inspect source files",
             "instruction": (f"List the files in {S}/sources and read them. Identify the "
                             "registry extract, the company blog, and any wire reprint "
                             "among them, and report what each source is."),
             "expected_effects": [],
             "contract": {"inputs": [{"source": "goal"}],
                          "tools": ["computer_list_directory", "computer_read_file"]}}
LISTING_RESULT = (f"I inspected registry_primary.txt, launch_blog.txt, and "
                  f"syndicated_wire.txt in {S}/sources.")
ONE_CLASS_RESULT = ("registry_primary.txt is the registry extract, the official "
                    "record. The other files are launch_blog.txt and syndicated_wire.txt.")
CLASSIFIED_RESULT = ("registry_primary.txt is the registry extract, the official "
                     "primary record. launch_blog.txt is the company blog, the "
                     "company's own announcement. syndicated_wire.txt is a wire "
                     "reprint of the blog, not an independent source.")

def outcome(sub, result):
    return M._outcome_from_task({"status": "done", "result": result}, sub)

_needs_classification = getattr(M, "_instruction_needs_classification",
                                lambda sub: False)

print("== cluster 3a: classification steps are judged as classifications ==")
check("the classify-all-sources step is a classification, not a discovery",
      _needs_classification(CLASS_SUB)
      and not M._instruction_needs_discovery(CLASS_SUB))
out = outcome(CLASS_SUB, LISTING_RESULT)
check("a bare listing of the candidates is not done",
      out["status"] == "failed" and out["verified_status"] == "contradicted", str(out))
check("the failure names the missing classification, not a matching file",
      any("classification" in e.lower() for e in out["evidence"])
      and not any("matching file" in e.lower() for e in out["evidence"]),
      str(out["evidence"]))
out = outcome(CLASS_SUB, ONE_CLASS_RESULT)
check("classifying only one of three sources is not done",
      out["status"] == "failed", str(out))
out = outcome(CLASS_SUB, CLASSIFIED_RESULT)
check("control: a per-source classification records done",
      out["status"] == "done", str(out))
single = {"title": "Find the matching file",
          "instruction": (f"List the text files in {S}/sources and inspect their first "
                          "lines. Report the absolute path of the unique file whose "
                          "first line starts with 'CODE:' and the exact text following "
                          "that prefix."),
          "expected_effects": [],
          "contract": {"inputs": [{"source": "goal"}], "tools": ["computer_read_file"]}}
check("control: an explicit single-target criterion stays a discovery",
      M._instruction_needs_discovery(single)
      and not _needs_classification(single))

print("== cluster 3a: end to end, the T4-L1 composition can now run ==")
OUT = S / "synthesis.md"
PLAN = {"subtasks": [
    {"title": CLASS_SUB["title"], "instruction": CLASS_SUB["instruction"],
     "expected_effects": [], "inputs": [{"source": "goal"}],
     "tools": ["computer_list_directory", "computer_read_file"]},
    {"title": "Write synthesis",
     "instruction": f"Write {OUT} reconciling the classified sources.",
     "expected_effects": [{"kind": "create", "path": str(OUT), "content": None}],
     "inputs": [{"source": "goal"}, {"source": "prior_step", "ref": "1"}],
     "tools": ["computer_write_file"]},
]}
GOAL = f"In {S}, inspect the sources, classify them, and write a synthesis to {OUT}."

class FakeAsk:
    def __init__(self, replies): self.replies = list(replies)
    async def __call__(self, prompt):
        return self.replies.pop(0) if self.replies else "{}"

class FakeQueue:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors); self.created = []; self.tasks = {}
    def create(self, request, title=None, origin=None):
        tid = f"task-cr{len(self.created) + 1}"
        self.created.append({"request": request, "title": title})
        self.tasks[tid] = {"id": tid, "behavior": self.behaviors[len(self.created) - 1]}
        return {"id": tid, "status": "queued", "title": title}
    def get(self, tid):
        rec = self.tasks.get(tid)
        if rec is None: return None
        b = rec["behavior"]
        if callable(b): return b(rec)
        return {"id": tid, "title": "", "request": "", **b}

async def e2e(step1_result):
    def write_step(rec):
        OUT.write_text("# Synthesis\n\nConfirmed: incorporated 2018-03-14.\n")
        return {"id": rec["id"], "title": "", "request": "", "status": "done",
                "result": f"Wrote synthesis to {OUT}."}
    q = FakeQueue([{"status": "done", "result": step1_result}, write_step])
    runner = M.MissionRunner(queue=q, ask=FakeAsk([json.dumps(PLAN)]), poll_sec=0.01)
    m = M.create_mission(GOAL)
    await runner.drive(m["id"])
    return M.get_mission(m["id"])

m = asyncio.run(e2e(CLASSIFIED_RESULT))
check("e2e: classification + synthesis completes (adjudication can run)",
      m["state"] == "done" and OUT.exists(), f"state={m['state']} reason={m.get('failure_reason')}")
OUT.unlink()
m = asyncio.run(e2e(LISTING_RESULT))
check("e2e: a listing-only step 1 still fails the mission honestly",
      m["state"] == "failed" and not OUT.exists(), f"state={m['state']}")

print("== cluster 3b: primary-over-secondary ranking is enforced ==")
RANK_SUB = {"title": "Research moon counts",
            "instruction": ("Find the current moon counts for Jupiter and Saturn and "
                            "report each count with the source name and URL. Rank "
                            "primary sources (the official record of the issuing "
                            "authority, such as NASA or the IAU) over secondary "
                            "coverage, and do not present a count sourced only to "
                            "secondary coverage as established."),
            "expected_effects": [],
            "contract": {"inputs": [{"source": "goal"}], "tools": ["web.run"]}}
L2_RESULT = ("Jupiter has 101 moons per PressReader (https://pressreader.example/item) "
             "and Saturn has 285 moons per The CSR Journal "
             "(https://csrjournal.example/item). These counts were not verified "
             "against NASA or the International Astronomical Union (IAU).")
SECONDARY_ONLY_RESULT = ("Jupiter has 101 moons per PressReader "
                         "(https://pressreader.example/item) and Saturn has 285 "
                         "moons per The CSR Journal (https://csrjournal.example/item).")
PRIMARY_RESULT = ("Jupiter has 95 moons per NASA, the primary authority record "
                  "(https://science.nasa.gov/jupiter/moons). Saturn has 146 moons "
                  "per NASA (https://science.nasa.gov/saturn/moons).")
prob = M._sourced_answer_problem(RANK_SUB, L2_RESULT)
check("the graded L2 shape (secondary counts flagged unverified) is rejected",
      prob is not None and "primary" in prob.lower(), str(prob))
prob = M._sourced_answer_problem(RANK_SUB, SECONDARY_ONLY_RESULT)
check("secondary-only counts with no primary source named are rejected",
      prob is not None and "primary" in prob.lower(), str(prob))
prob = M._sourced_answer_problem(RANK_SUB, PRIMARY_RESULT)
check("control: counts established from the primary authority pass",
      prob is None, str(prob))
plain = {"title": "Research Jupiter moons",
         "instruction": ("Find a reliable source for Jupiter's moon count and report "
                         "the count, the source name and URL."),
         "expected_effects": [],
         "contract": {"inputs": [{"source": "goal"}], "tools": ["web.run"]}}
prob = M._sourced_answer_problem(plain, SECONDARY_ONLY_RESULT)
check("control: a step that demands no primary ranking keeps the old disposition",
      prob is None, str(prob))
check("planner guidance: the plan prompt carries the primary-ranking rule",
      "ranking primary sources" in M.PLAN_PROMPT
      and "syndicated reprint is not an independent confirmation" in M.PLAN_PROMPT)
check("planner guidance: the plan prompt separates classification from discovery",
      "not as a search for one matching file" in M.PLAN_PROMPT)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} checks passed", flush=True)
if failed:
    print("FAILED: " + ", ".join(failed), flush=True)
    sys.exit(1)
print("ALL TIER 4 CLASSIFICATION + RANKING CHECKS PASSED", flush=True)
