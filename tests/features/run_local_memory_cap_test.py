"""
FIX C: the local-model memory budget gate (defect: DEFECT-LOCAL-MODEL-MEMORY-CAP-2026-11-02).

Reproduces the 2026-11-02 shape in miniature and proves the fix, failing-first:
on the pristine tree this suite still runs its pre-fix control (C) and then fails,
because the gate does not exist there; with FIX C every part passes.

  A  the budget math itself: 40%-of-RAM cap and the 25% free floor, replace
     semantics, and the file-size + context-allowance estimate calibrated on the
     incident (a 4.7 GB file was observed resident at 6.43 GB)
  B  the gate: a first 6 GB-class load is allowed and ledgered; a SECOND 6 GB-class
     load in another engine is refused, the ledger names the budget as the reason,
     and with no cloud configured it is queued as waiting for memory; with a cloud
     configured the same refusal routes to cloud instead of queueing
  C  the control: an UNGATED loader (what the old load paths did) proceeds with
     the second load on identical state - the defect, reproduced
  D  pressure: a memory-pressure unload frees idle Xod-managed models largest
     first, spares the answering model, and never touches a model Xod did not
     load (it comes back as a suggestion instead)
  E  the grace timer: a Xod-loaded model idle past the grace period while the
     answering provider is elsewhere is unloaded; a recently used one stays
  F  cloud-unaffected proof: a cloud-brain session's only contact with the gate
     layer (reconcile + status) writes zero load events to the ledger and leaves
     a resident local model exactly as it found it
  G  the wiring: llm_service reroutes a budget-refused built-in chat to the
     configured cloud endpoint (and raises the honest refusal with none), and
     load_lmstudio_model refuses at the gate before touching LM Studio
  H  the probe-free quick gate used on hot paths (embeddings)

The engines are fakes: an in-memory "world" the observer reads and the unloder
edits, on a fake 16 GB machine - the incident's machine. No real model loads.

Run: .venv/bin/python tests/features/run_local_memory_cap_test.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ["XOD_DATA_DIR"] = os.environ.get("XOD_TEST_DATA_DIR") or tempfile.mkdtemp(prefix="xod-memcap-")
sys.path.insert(0, str(ROOT))

from app.paths import DATA_DIR  # noqa: E402

RUN = DATA_DIR / "test_runs" / "model_cap"
RUN.mkdir(parents=True, exist_ok=True)
results = []


def report(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


class World:
    """The fake machine's engines, as the engines' own APIs would report them."""

    def __init__(self):
        self.engines = {"builtin": {}, "lm_studio": {}, "ollama": {}}
        self.answering = {"provider": "", "model": ""}
        self.unloaded = []

    def total(self):
        return round(sum(gb for models in self.engines.values() for gb in models.values()), 2)

    def snapshot(self):
        return {"answering": dict(self.answering),
                "engines": {e: {"running": bool(models),
                                "models": [{"model": m, "gb": gb,
                                            "in_use": self.answering["provider"] == e}
                                           for m, gb in models.items()]}
                            for e, models in self.engines.items()}}


def ungated_load(world, engine, model, gb):
    """What the pre-fix load paths did: load, unconditionally (defect control)."""
    world.engines[engine][model] = gb


try:
    from app.services import model_budget as MB
    HAVE_GATE = True
except ImportError:
    HAVE_GATE = False


def make_budget(world, machine, cloud=False, name="b"):
    async def observer():
        return world.snapshot()

    async def unloder(engine, model):
        world.engines.get(engine, {}).pop(model, None)
        world.unloaded.append((engine, model))
        return True

    return MB.ModelBudget(machine_fn=lambda: dict(machine), observer=observer,
                          unloder=unloder, cloud_fn=lambda: cloud,
                          ledger_path=RUN / f"{name}.jsonl",
                          queue_path=RUN / f"{name}-queue.json")


def ledger_kinds(budget):
    try:
        return [json.loads(x)["kind"] for x in budget._ledger_path.read_text().splitlines() if x.strip()]
    except OSError:
        return []


async def gated_load(budget, world, engine, model, est, observed):
    d = await budget.check_load(engine, model, est, "test")
    if d["allowed"]:
        world.engines[engine][model] = observed
        budget.note_loaded(engine, model, observed, "test")
    return d


async def amain():
    # ---------------------------------------------------------------- A: the math
    v = MB.evaluate_budget(16.0, 13.0, 0.0, 6.0)
    report("A a single 6 GB-class load on a fresh 16 GB machine is allowed",
           v["allowed"], f"{v}")
    v = MB.evaluate_budget(16.0, 9.0, 6.43, 6.89)
    report("A the incident shape is refused on BOTH the cap and the free floor",
           not v["allowed"] and len(v["reasons"]) == 2
           and "budget" in v["reasons"][0] and "floor" in v["reasons"][1], f"{v['reasons']}")
    v = MB.evaluate_budget(16.0, 10.5, 6.0, 6.0, replace_gb=6.0)
    report("A a same-engine swap that frees its predecessor first is allowed",
           v["allowed"], f"{v}")
    est = MB.estimate_gb(4.7)
    report("A the estimate is file size + context allowance (incident-calibrated)",
           4.7 < est and abs(est - 6.43) < 0.25, f"est={est} for the 4.7 GB file observed at 6.43 GB")

    # ---------------------------------------------------------------- B: the gate
    world, machine = World(), {"total_gb": 16.0, "available_gb": 13.0}
    b = make_budget(world, machine, name="gate")
    d = await gated_load(b, world, "ollama", "qwen2.5-coder-7b", 6.0, 6.43)
    report("B first 6 GB-class load allowed and ledgered",
           d["allowed"] and world.total() == 6.43
           and "load_allowed" in ledger_kinds(b) and "loaded" in ledger_kinds(b),
           f"kinds={ledger_kinds(b)}")
    machine["available_gb"] = 9.0
    d = await gated_load(b, world, "lm_studio", "gemma-4-12b", MB.estimate_gb(5.1), 6.5)
    q = b._queue_read()
    report("B second 6 GB-class load in another engine is refused, nothing loads",
           not d["allowed"] and "gemma-4-12b" not in world.engines["lm_studio"]
           and any("budget" in r for r in d["reasons"]), f"{d['reasons']}")
    report("B with no cloud configured it queues as waiting for memory",
           d["route"] == "queue" and d["queued"] and len(q) == 1
           and q[0]["status"] == "waiting_for_memory"
           and "load_refused" in ledger_kinds(b) and "queued" in ledger_kinds(b),
           f"queue={q}")
    st = b.budget_status()
    report("B budget status names resident, budget and the queue",
           st["resident_gb"] == 6.43 and st["budget_gb"] == 6.4 and len(st["queue"]) == 1
           and st["engines"][2]["state"] == "resident", f"{st['summary']}")

    bc = make_budget(world, machine, cloud=True, name="gate-cloud")
    d = await bc.check_load("lm_studio", "gemma-4-12b", MB.estimate_gb(5.1), "test")
    report("B with a cloud configured the same refusal routes to cloud, not the queue",
           not d["allowed"] and d["route"] == "cloud" and not d["queued"]
           and bc._queue_read() == [], f"route={d['route']}")

    w2, m2 = World(), {"total_gb": 16.0, "available_gb": 10.5}
    w2.engines["lm_studio"]["old-model"] = 6.0
    b2 = make_budget(w2, m2, name="gate-replace")
    d = await b2.check_load("lm_studio", "new-model", 6.0, "test")
    report("B an LM Studio load that unloads its predecessor first is allowed",
           d["allowed"] and d["projected_gb"] == 6.0, f"projected={d.get('projected_gb')}")
    d = await b.check_load("ollama", "qwen2.5-coder-7b", 6.0, "test")
    report("B an already-resident model is not a load and is always allowed",
           d["allowed"] and d.get("already_resident"), f"{d}")

    # ---------------------------------------------------------------- D: pressure
    w3, m3 = World(), {"total_gb": 16.0, "available_gb": 5.0}
    w3.answering = {"provider": "builtin", "model": "builtin-repo"}
    w3.engines["builtin"]["builtin-repo"] = 7.0
    w3.engines["ollama"].update({"managed-idle": 6.0, "someone-elses": 5.0})
    w3.engines["lm_studio"]["managed-lms"] = 4.0
    b3 = make_budget(w3, m3, name="pressure")
    for eng, mod, gb in (("builtin", "builtin-repo", 7.0), ("ollama", "managed-idle", 6.0),
                         ("lm_studio", "managed-lms", 4.0)):
        b3.note_loaded(eng, mod, gb, "test")
    await b3.reconcile()                     # folds in the unmanaged model as observed
    for key in ("ollama|managed-idle", "lm_studio|managed-lms"):
        b3._resident[key]["last_used_at"] = time.time() - 3600
    res = await b3.pressure_unload()
    report("D pressure unloads idle Xod-managed models, largest first",
           [a["model"] for a in res["actions"]] == ["managed-idle", "managed-lms"]
           and all(a["ok"] for a in res["actions"])
           and "managed-idle" not in w3.engines["ollama"]
           and "managed-lms" not in w3.engines["lm_studio"], f"actions={res['actions']}")
    report("D the answering model and the model Xod did not load are untouched",
           "builtin-repo" in w3.engines["builtin"] and "someone-elses" in w3.engines["ollama"]
           and [s["model"] for s in res["suggestions"]] == ["someone-elses"],
           f"suggestions={res['suggestions']}")
    kinds = ledger_kinds(b3)
    report("D the ledger records the pressure unloads and the suggestion",
           kinds.count("unloaded") >= 2 and "pressure_suggestion" in kinds, f"kinds={kinds}")

    # ---------------------------------------------------------------- E: grace timer
    w4, m4 = World(), {"total_gb": 16.0, "available_gb": 11.0}
    w4.answering = {"provider": "openai", "model": "gpt-4o"}
    w4.engines["lm_studio"]["idle-lms"] = 4.0
    w4.engines["ollama"]["fresh-ollama"] = 3.0
    b4 = make_budget(w4, m4, name="idle")
    b4.note_loaded("lm_studio", "idle-lms", 4.0, "test")
    b4.note_loaded("ollama", "fresh-ollama", 3.0, "test")
    await b4.reconcile()
    b4._resident["lm_studio|idle-lms"]["last_used_at"] = time.time() - 3600
    acts = await b4.check_idle_unloads(15)
    report("E a Xod-loaded model idle past the grace period is unloaded; a fresh one stays",
           [a["model"] for a in acts] == ["idle-lms"]
           and "idle-lms" not in w4.engines["lm_studio"]
           and "fresh-ollama" in w4.engines["ollama"], f"actions={acts}")

    # ---------------------------------------------------------------- F: cloud proof
    w5, m5 = World(), {"total_gb": 16.0, "available_gb": 9.0}
    w5.answering = {"provider": "openai", "model": "gpt-4o"}
    w5.engines["ollama"]["resident-from-before"] = 6.0
    b5 = make_budget(w5, m5, name="cloud")
    await b5.reconcile()                     # all a cloud session ever does here
    st = b5.budget_status()
    kinds = ledger_kinds(b5)
    report("F a cloud-brain session writes zero load events and disturbs nothing",
           kinds == ["observed_load"] and w5.total() == 6.0
           and st["resident_gb"] == 6.0
           and st["engines"][2]["models"][0]["managed"] is False, f"kinds={kinds}")

    # ---------------------------------------------------------------- G: the wiring
    from app.services import llm as llm_mod
    from app.services.local_engine import local_engine
    # R11 fixture note: part G drives local chats through the shared funnel.
    # Under the cloud-first policy a fresh data dir defaults local inference
    # OFF; this suite models an install that opted in, so the budget gate —
    # the thing under test here — is what decides.
    from app.services import inference_policy
    inference_policy.set_allowed(True, source="test_fixture")
    svc = llm_mod.llm_service
    saved = (svc.get_available_tiers, svc._build_endpoint, local_engine.ensure_running)
    decision = {"allowed": False, "engine": "builtin", "model": "m", "est_gb": 7.0,
                "route": "queue", "queued": True,
                "reasons": ["local models would hold 13.0 GB, over the 6.4 GB budget (40% of 16 GB RAM)"]}

    async def refuse(repo):
        raise MB.ModelBudgetRefused(decision)

    try:
        local_engine.ensure_running = refuse
        svc.get_available_tiers = lambda: {"cloud_ready": True, "cloud_options":
                                           [{"provider": "openai", "model": "gpt-4o"}]}
        svc._build_endpoint = lambda provider, model=None, **kw: {
            "provider": provider, "model": model,
            "base_url": "https://api.openai.com/v1", "api_key": "k"}
        info, _ = await svc._ensure_local_or_route(
            {"provider": "xod", "model": "m", "base_url": "http://127.0.0.1", "api_key": "x"})
        report("G a budget-refused built-in chat reroutes to the configured cloud",
               info["provider"] == "openai", f"provider={info['provider']}")
        svc.get_available_tiers = lambda: {"cloud_ready": False, "cloud_options": []}
        raised = False
        try:
            await svc._ensure_local_or_route(
                {"provider": "xod", "model": "m", "base_url": "http://127.0.0.1", "api_key": "x"})
        except MB.ModelBudgetRefused:
            raised = True
        report("G with no cloud configured the refusal raises, honestly", raised)

        async def fake_status():
            return {"online": True, "loaded_model": None,
                    "models": [{"key": "big-model", "size_gb": 5.0, "type": "llm",
                                "is_loaded": False}]}
        w6 = World()
        w6.engines["ollama"]["qwen2.5-coder-7b"] = 6.0
        single = MB.model_budget
        saved_single = (single._machine_fn, single._observer, single._cloud_fn,
                        svc.get_lmstudio_status)
        single._machine_fn = lambda: {"total_gb": 16.0, "available_gb": 9.0}

        async def obs6():
            return w6.snapshot()
        single._observer = obs6
        single._cloud_fn = lambda: False
        svc.get_lmstudio_status = fake_status
        try:
            r = await svc.load_lmstudio_model("big-model")
        finally:
            (single._machine_fn, single._observer, single._cloud_fn,
             svc.get_lmstudio_status) = saved_single
        report("G load_lmstudio_model refuses at the gate before touching LM Studio",
               r.get("success") is False and r.get("budget_refused") is True,
               f"{r.get('message', '')[:110]}")
    finally:
        svc.get_available_tiers, svc._build_endpoint, local_engine.ensure_running = saved

    # ------------------------------------------------- header surface carries it
    from app.services import local_servers
    rep = await local_servers.report()
    report("G the header report carries the budget block",
           isinstance(rep.get("budget"), dict) and "summary" in rep["budget"],
           f"{(rep.get('budget') or {}).get('summary')}")

    # ---------------------------------------------------------------- H: quick gate
    w7, m7 = World(), {"total_gb": 16.0, "available_gb": 13.0}
    b7 = make_budget(w7, m7, name="quick")
    b7.note_loaded("ollama", "resident", 6.0, "test")
    d1 = b7.quick_check("ollama", "another-big-one", 6.5, "test")
    d2 = b7.quick_check("ollama", "resident", 6.0, "test")
    report("H the probe-free quick gate refuses a second big load, passes a resident one",
           not d1["allowed"] and d1["queued"] and d2["allowed"] and d2.get("already_resident"),
           f"d1_route={d1['route']}")


def main():
    # --- C runs either way: the pre-fix shape --------------------------------
    w = World()
    ungated_load(w, "lm_studio", "gemma-4-12b", 6.5)
    ungated_load(w, "ollama", "qwen2.5-coder-7b", 6.43)
    report("C control: an ungated loader proceeds with the second 6 GB-class load",
           w.total() > 12.9, f"total resident {w.total()} GB on a 16 GB machine (the 2026-11-02 defect)")
    if not HAVE_GATE:
        report("the FIX C gate exists (app.services.model_budget)", False,
               "ImportError on this tree: the second load has nothing to stop it")
    else:
        asyncio.run(amain())
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed", flush=True)
    if failed:
        print("FAILED:", "; ".join(failed), flush=True)
        sys.exit(1)
    print("ALL LOCAL MEMORY CAP TESTS PASSED", flush=True)


if __name__ == "__main__":
    main()
