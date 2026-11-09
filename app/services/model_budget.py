"""
FIX C: a memory budget for local models, enforced before any load, with one ledger
for every engine (defect: DEFECT-LOCAL-MODEL-MEMORY-CAP-2026-11-02; lifecycle:
LOCAL-ENGINE-LIFECYCLE-2026-10-31).

The 2026-11-02 incident, in one line: LM Studio held a 6.5 GB Gemma and Ollama held a
6.4 GB qwen on a 16 GB Mac at the same time; the models' GPU/unified footprint lands in
wired memory, which cannot page out, so everything else swapped until apps died. Each
engine had decided on its own. Nothing totalled the footprints against the machine.

What lives here:

  The gate (check_load / require_load): before ANY Xod-initiated local load (the
  built-in engine starting, an LM Studio load, an Ollama model a chat/vision/embedding
  request is about to pull in), the projected TOTAL local footprint after the load is
  checked against the machine: at most 40% of physical RAM across all engines
  combined, and the load must still leave at least 25% of RAM free. Over budget the
  load does not happen: the decision routes the work to the configured cloud provider,
  or, with no cloud configured, the request is queued with an honest
  "waiting for memory" status (see the queue below). Never a silent failure, never a
  partial load, never "load anyway".

  The ledger (DATA_DIR/model-load-ledger.jsonl, next to usage_ledger.jsonl): every
  load, unload, refusal, queueing and externally-observed load/unload across the
  built-in engine, Ollama and LM Studio, with engine, model, estimated footprint,
  observed footprint and whether Xod initiated it. The gate reads the same ledger, so
  the engines can no longer make independent decisions that sum past the machine.
  Engines Xod does not manage are OBSERVED into the ledger (reconcile()), never
  silently controlled.

  Idle unload, completed: the built-in engine's own idle stop stays in
  local_servers; this module adds the cross-engine half of the Oct 31 policy. Models
  XOD loaded into LM Studio / Ollama are unloaded after the grace period while the
  answering provider is elsewhere, and a memory-pressure event (surfaced by Computer
  Health) unloads idle Xod-managed models immediately, largest first. An engine the
  user started outside Xod keeps its app and its own models: those surface as
  suggestions (Tier 2, via Computer Health / the budget status), never an auto-unload
  and never a silent quit.

  Visibility (budget_status()): per-engine state, model and footprint against the
  budget, plus the waiting-for-memory queue. local_servers.report() carries it to the
  header surface, GET /api/model-budget serves it, and the Computer Health readout
  speaks the totals.

Estimates: a model's footprint is estimated as its file size plus a context
allowance (35% of file size, at least 0.5 GB, for the KV cache and runtime overhead).
Calibrated on the incident itself: the qwen's 4.7 GB file was observed resident at
6.43 GB (file x 1.37). The ledger records the observed footprint after every load, so
a bad estimate is visible, never hidden.

The budget is per-machine (the defect's own words): the defaults are the 40% cap
and the 25% free floor, and XOD_MODEL_BUDGET_FRAC / XOD_MODEL_FREE_FLOOR_FRAC
override them for a machine whose owner decides otherwise. XOD_MODEL_BUDGET=off
turns the gate and the model unloads off entirely (the ledger still records what
the load paths do). Test suites that fake the hardware (a stand-in Ollama whose
"4.5 GB model" consumes no memory) set the off switch for their sandbox, the way
they already set XOD_SPEECH=off; production keeps the defaults.
"""
import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.paths import DATA_DIR

logger = logging.getLogger("xod.model_budget")

GB = 1024 ** 3
BUDGET_FRAC = 0.40            # total local footprint, all engines, <= 40% of RAM
FREE_FLOOR_FRAC = 0.25        # a load must still leave >= 25% of RAM free
CONTEXT_ALLOWANCE_FRAC = 0.35
CONTEXT_ALLOWANCE_MIN_GB = 0.5
PRESSURE_IDLE_SEC = 60        # under pressure, "idle" means unused for this long
PENDING_CONFIRM_SEC = 180     # an approved load the observer never sees within this
                              # window is treated as a load that did not land

LEDGER_PATH = DATA_DIR / "model-load-ledger.jsonl"
QUEUE_PATH = DATA_DIR / "model-budget-queue.json"

ENGINES = ("builtin", "lm_studio", "ollama")


def estimate_gb(file_gb: float) -> float:
    """Estimated resident footprint of a model from its file size (see module docstring)."""
    return round(file_gb + max(CONTEXT_ALLOWANCE_MIN_GB, file_gb * CONTEXT_ALLOWANCE_FRAC), 2)


def evaluate_budget(total_gb: float, available_gb: float, resident_gb: float,
                    new_gb: float, replace_gb: float = 0.0,
                    budget_frac: float = BUDGET_FRAC,
                    free_floor_frac: float = FREE_FLOOR_FRAC) -> Dict[str, Any]:
    """The budget math, pure. replace_gb is the footprint the load path itself frees
    first (the built-in engine stops its old model before starting a new one; an LM
    Studio load unloads the currently loaded model first; Ollama replaces nothing)."""
    budget_gb = total_gb * budget_frac
    projected_gb = resident_gb - replace_gb + new_gb
    free_after_gb = available_gb - new_gb
    free_floor_gb = total_gb * free_floor_frac
    reasons = []
    if projected_gb > budget_gb:
        reasons.append(
            f"local models would hold {projected_gb:.1f} GB, over the {budget_gb:.1f} GB "
            f"budget (40% of {total_gb:.0f} GB RAM)")
    # A free floor of 0 turns this check off (the per-machine override's "no
    # floor"); the production default is 0.25 and the check always applies there.
    if free_floor_frac > 0 and free_after_gb < free_floor_gb:
        reasons.append(
            f"loading would leave {free_after_gb:.1f} GB free, under the "
            f"{free_floor_gb:.1f} GB floor (25% of RAM)")
    return {"allowed": not reasons, "reasons": reasons, "budget_gb": round(budget_gb, 2),
            "projected_gb": round(projected_gb, 2), "free_after_gb": round(free_after_gb, 2),
            "free_floor_gb": round(free_floor_gb, 2)}


class ModelBudgetRefused(RuntimeError):
    """A local load the budget refused. Carries the decision so callers can route the
    work (cloud) or show the honest status (queued, waiting for memory)."""

    def __init__(self, decision: Dict[str, Any]):
        self.decision = decision
        d = decision
        msg = (f"Not loading {d['model']} in {d['engine']}: " + "; ".join(d["reasons"]) + ".")
        if d.get("route") == "cloud":
            msg += " A cloud model is configured, so this work can run there instead."
        elif d.get("queued"):
            msg += " It is queued as waiting for memory."
        super().__init__(msg)


# ---------------------------------------------------------------- defaults (real machine)

def _machine() -> Dict[str, float]:
    import psutil
    vm = psutil.virtual_memory()
    return {"total_gb": round(vm.total / GB, 2), "available_gb": round(vm.available / GB, 2)}


def _cloud_configured() -> bool:
    try:
        from app.services.llm import llm_service
        return bool(llm_service.get_available_tiers().get("cloud_ready"))
    except Exception:
        return False


async def _observe() -> Dict[str, Any]:
    """Live per-engine resident models, from the engines' own APIs (observed footprint
    is what the engine reports: Ollama's resident size, LM Studio's model size, the
    built-in engine's process-family RSS)."""
    from app.services import local_servers
    snap: Dict[str, Any] = {"answering": local_servers.answering(),
                            "engines": {e: {"running": False, "models": []} for e in ENGINES}}
    try:
        from app.services.local_engine import local_engine
        if local_engine.running() and local_engine.repo:
            gb = local_servers._family_rss_gb(local_engine.proc.pid) if local_engine.proc else None
            snap["engines"]["builtin"] = {"running": True, "models": [
                {"model": local_engine.repo, "gb": gb, "in_use": snap["answering"]["provider"] == "xod"}]}
    except Exception as e:
        logger.debug(f"builtin observe failed: {e}")
    try:
        from app.services.llm import llm_service
        st = await llm_service.get_lmstudio_status()
        if st.get("online"):
            models = [{"model": m["key"], "gb": m.get("size_gb"),
                       "in_use": snap["answering"]["provider"] == "lm_studio"}
                      for m in st.get("models", []) if m.get("is_loaded") and m.get("type") != "embedding"]
            snap["engines"]["lm_studio"] = {"running": True, "models": models}
    except Exception as e:
        logger.debug(f"lm_studio observe failed: {e}")
    try:
        import httpx
        from app.services.ollama_hub import root
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(f"{root()}/api/ps")
        if r.status_code == 200:
            models = [{"model": m.get("name"), "gb": round((m.get("size") or 0) / GB, 2),
                       "in_use": snap["answering"]["provider"] == "ollama"}
                      for m in r.json().get("models", []) if "embed" not in (m.get("name") or "")]
            snap["engines"]["ollama"] = {"running": True, "models": models}
    except Exception as e:
        logger.debug(f"ollama observe failed: {e}")
    return snap


async def _unload(engine: str, model: str) -> bool:
    """Unload ONE model from an engine Xod manages models in. Never quits an app:
    LM Studio and Ollama themselves keep running; only the model leaves memory."""
    try:
        if engine == "builtin":
            from app.services.local_engine import local_engine
            if local_engine.repo == model:
                await local_engine.stop()
                return True
            return False
        if engine == "lm_studio":
            from app.services.llm import llm_service
            st = await llm_service.get_lmstudio_status()
            for m in st.get("models", []):
                if m.get("key") == model and m.get("instance_id"):
                    r = await llm_service.unload_lmstudio_model(m["instance_id"])
                    return bool(r.get("success"))
            return False
        if engine == "ollama":
            import httpx
            from app.services.ollama_hub import root
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{root()}/api/generate", json={"model": model, "keep_alive": 0})
            return r.status_code < 300
    except Exception as e:
        logger.warning(f"unload {engine}/{model} failed: {e}")
    return False


# ---------------------------------------------------------------- the gate + ledger

class ModelBudget:
    def __init__(self, machine_fn: Callable[[], Dict[str, float]] = _machine,
                 observer: Optional[Callable[[], Any]] = None,
                 unloder: Optional[Callable[[str, str], Any]] = None,
                 cloud_fn: Callable[[], bool] = _cloud_configured,
                 ledger_path: Path = LEDGER_PATH, queue_path: Path = QUEUE_PATH,
                 budget_frac: float = BUDGET_FRAC, free_floor_frac: float = FREE_FLOOR_FRAC):
        self._machine_fn = machine_fn
        self._observer = observer or _observe
        self._unloder = unloder or _unload
        self._cloud_fn = cloud_fn
        self._ledger_path = Path(ledger_path)
        self._queue_path = Path(queue_path)
        self._budget_frac = budget_frac
        self._free_floor_frac = free_floor_frac
        self._lock = threading.Lock()
        self._resident: Dict[str, Dict[str, Any]] = {}   # "engine|model" -> entry
        self._pending: Dict[str, float] = {}             # approved loads not yet observed
        self._loaded = False

    # ------------------------------------------------------------ ledger IO

    def _append(self, event: Dict[str, Any]) -> None:
        event = {"at": time.time(), **event}
        try:
            with self._lock:
                self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._ledger_path, "a") as fh:
                    fh.write(json.dumps(event) + "\n")
        except Exception as e:
            logger.warning(f"model ledger write failed: {e}")

    def _ensure_state(self) -> None:
        """Resident state is the ledger, replayed. Loaded once, then kept live."""
        if self._loaded:
            return
        self._loaded = True
        try:
            for line in self._ledger_path.read_text().splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                self._replay(ev)
        except OSError:
            pass

    def _replay(self, ev: Dict[str, Any]) -> None:
        kind, key = ev.get("kind"), f"{ev.get('engine')}|{ev.get('model')}"
        if kind in ("loaded", "observed_load"):
            prev = self._resident.get(key, {})
            self._resident[key] = {
                "engine": ev.get("engine"), "model": ev.get("model"),
                "est_gb": ev.get("est_gb") or prev.get("est_gb"),
                "observed_gb": ev.get("observed_gb", prev.get("observed_gb")),
                "managed": bool(ev.get("managed", prev.get("managed", False))),
                "loaded_at": ev.get("at") or prev.get("loaded_at"),
                "last_used_at": prev.get("last_used_at") or ev.get("at"),
            }
            self._pending.pop(key, None)
        elif kind in ("unloaded", "observed_unload", "load_failed"):
            self._resident.pop(key, None)
            self._pending.pop(key, None)

    # ------------------------------------------------------------ queue (waiting for memory)

    def _queue_read(self) -> List[Dict[str, Any]]:
        try:
            return json.loads(self._queue_path.read_text())
        except Exception:
            return []

    def _queue_write(self, rows: List[Dict[str, Any]]) -> None:
        try:
            self._queue_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._queue_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(rows, indent=1))
            tmp.replace(self._queue_path)
        except Exception as e:
            logger.warning(f"model queue write failed: {e}")

    def _queue_add(self, decision: Dict[str, Any]) -> None:
        rows = [r for r in self._queue_read() if not (
            r["engine"] == decision["engine"] and r["model"] == decision["model"])]
        rows.append({"engine": decision["engine"], "model": decision["model"],
                     "est_gb": decision["est_gb"], "at": time.time(),
                     "status": "waiting_for_memory", "reasons": decision["reasons"]})
        self._queue_write(rows)

    def _queue_clear(self, engine: str, model: str) -> None:
        rows = self._queue_read()
        kept = [r for r in rows if not (r["engine"] == engine and r["model"] == model)]
        if len(kept) != len(rows):
            self._queue_write(kept)

    # ------------------------------------------------------------ state views

    @staticmethod
    def _footprint(entry: Dict[str, Any]) -> float:
        gb = entry.get("observed_gb")
        return float(gb) if gb is not None else float(entry.get("est_gb") or 0.0)

    def resident(self) -> List[Dict[str, Any]]:
        self._ensure_state()
        return [{**e, "gb": round(self._footprint(e), 2)} for e in self._resident.values()]

    def resident_gb(self) -> float:
        return round(sum(self._footprint(e) for e in self.resident()), 2)

    def budget_status(self) -> Dict[str, Any]:
        """Per-engine state, model and footprint against the budget, plus the queue.
        Sync and probe-free: it reads the ledger state and the machine's counters, so
        the header can render it any time. reconcile() keeps the state honest."""
        self._ensure_state()
        m = self._machine_fn()
        total, avail = m["total_gb"], m["available_gb"]
        resident = self.resident_gb()
        budget_gb = round(total * self._budget_frac, 2)
        engines = []
        for eng in ENGINES:
            models = [e for e in self.resident() if e["engine"] == eng]
            engines.append({
                "engine": eng,
                "state": "resident" if models else "off",
                "models": [{"model": e["model"], "gb": e["gb"], "managed": e["managed"],
                            "last_used_at": e.get("last_used_at")} for e in models],
                "gb": round(sum(e["gb"] for e in models), 2),
            })
        return {
            "total_ram_gb": total, "available_gb": avail,
            "budget_gb": budget_gb, "budget_frac": self._budget_frac,
            "free_floor_gb": round(total * self._free_floor_frac, 2),
            "resident_gb": resident, "over_budget": resident > budget_gb,
            "engines": engines,
            "queue": self._queue_read(),
            "summary": (f"Local models are holding {resident:.1f} GB of the {budget_gb:.1f} GB "
                        f"budget on this {total:.0f} GB machine."),
        }

    def recent_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            lines = self._ledger_path.read_text().splitlines()[-limit:]
            return [json.loads(x) for x in lines if x.strip()]
        except Exception:
            return []

    # ------------------------------------------------------------ observation

    async def reconcile(self) -> Dict[str, Any]:
        """Fold what the engines actually hold into the ledger. Models Xod did not
        load are recorded as observed (managed False): the ledger is the cross-engine
        truth, and observing is not controlling."""
        self._ensure_state()
        snap = await self._observer()
        seen = set()
        for eng, info in (snap.get("engines") or {}).items():
            for m in info.get("models", []):
                if not m.get("model"):
                    continue
                key = f"{eng}|{m['model']}"
                seen.add(key)
                cur = self._resident.get(key)
                if cur is None:
                    managed = key in self._pending
                    self._resident[key] = {
                        "engine": eng, "model": m["model"], "est_gb": None,
                        "observed_gb": m.get("gb"), "managed": managed,
                        "loaded_at": time.time(), "last_used_at": time.time(),
                    }
                    self._append({"kind": "observed_load", "engine": eng, "model": m["model"],
                                  "observed_gb": m.get("gb"), "managed": managed,
                                  "source": "reconcile"})
                    if managed:
                        self._queue_clear(eng, m["model"])
                elif m.get("gb") is not None:
                    cur["observed_gb"] = m["gb"]
                self._pending.pop(key, None)
        now = time.time()
        for key, entry in list(self._resident.items()):
            if key in seen:
                continue
            pend_at = self._pending.get(key)
            if pend_at is not None and now - pend_at < PENDING_CONFIRM_SEC:
                continue                      # an approved load that is still landing
            del self._resident[key]
            self._append({"kind": "observed_unload", "engine": entry["engine"],
                          "model": entry["model"], "managed": entry["managed"],
                          "source": "reconcile"})
        return snap

    # ------------------------------------------------------------ the gate

    def _replace_gb(self, engine: str, model: str) -> float:
        """What a load in this engine frees first, by that engine's own load path."""
        if engine == "ollama":
            return 0.0                        # Ollama keeps models side by side
        return round(sum(self._footprint(e) for e in self._resident.values()
                         if e["engine"] == engine and e["model"] != model), 2)

    async def check_load(self, engine: str, model: str, est_gb: float, source: str,
                         replace_gb: Optional[float] = None) -> Dict[str, Any]:
        """The gate. Returns the decision; require_load() is the raising form.
        An already-resident model is not a load and is always allowed."""
        if not _enabled():
            return {"allowed": True, "disabled": True, "engine": engine, "model": model,
                    "est_gb": est_gb, "route": "load", "queued": False, "reasons": []}
        self._ensure_state()
        await self.reconcile()
        key = f"{engine}|{model}"
        if key in self._resident:
            self.note_used(engine, model)
            return {"allowed": True, "already_resident": True, "engine": engine,
                    "model": model, "est_gb": est_gb, "route": "load", "queued": False,
                    "reasons": []}
        m = self._machine_fn()
        rep = self._replace_gb(engine, model) if replace_gb is None else replace_gb
        verdict = evaluate_budget(m["total_gb"], m["available_gb"], self.resident_gb(),
                                   est_gb, rep, self._budget_frac, self._free_floor_frac)
        decision = {"allowed": verdict["allowed"], "engine": engine, "model": model,
                    "est_gb": est_gb, "source": source, "route": "load", "queued": False,
                    "reasons": verdict["reasons"], **{k: verdict[k] for k in
                    ("budget_gb", "projected_gb", "free_after_gb", "free_floor_gb")}}
        if verdict["allowed"]:
            self._pending[key] = time.time()
            self._resident[key] = {"engine": engine, "model": model, "est_gb": est_gb,
                                   "observed_gb": None, "managed": True,
                                   "loaded_at": time.time(), "last_used_at": time.time()}
            self._append({"kind": "load_allowed", "engine": engine, "model": model,
                          "est_gb": est_gb, "managed": True, "source": source,
                          "projected_gb": verdict["projected_gb"],
                          "budget_gb": verdict["budget_gb"]})
            self._queue_clear(engine, model)
        else:
            decision["route"] = "cloud" if self._cloud_fn() else "queue"
            decision["queued"] = decision["route"] == "queue"
            self._append({"kind": "load_refused", "engine": engine, "model": model,
                          "est_gb": est_gb, "managed": True, "source": source,
                          "route": decision["route"], "reasons": verdict["reasons"],
                          "projected_gb": verdict["projected_gb"],
                          "budget_gb": verdict["budget_gb"],
                          "free_after_gb": verdict["free_after_gb"]})
            if decision["queued"]:
                self._queue_add(decision)
                self._append({"kind": "queued", "engine": engine, "model": model,
                              "est_gb": est_gb, "source": source,
                              "status": "waiting_for_memory"})
            logger.warning(f"model budget refused {engine}/{model} ({source}): "
                           + "; ".join(verdict["reasons"]))
        return decision

    async def require_load(self, engine: str, model: str, est_gb: float, source: str,
                           replace_gb: Optional[float] = None) -> Dict[str, Any]:
        decision = await self.check_load(engine, model, est_gb, source, replace_gb)
        if not decision["allowed"]:
            raise ModelBudgetRefused(decision)
        return decision

    def quick_check(self, engine: str, model: str, est_gb: float, source: str) -> Dict[str, Any]:
        """The gate without probes, for hot paths (embeddings): ledger state plus the
        machine's live counters only. A refusal is recorded at most once a minute per
        model so a hot loop cannot flood the ledger."""
        if not _enabled():
            return {"allowed": True, "disabled": True, "engine": engine, "model": model,
                    "est_gb": est_gb, "route": "load", "queued": False, "reasons": []}
        self._ensure_state()
        key = f"{engine}|{model}"
        if key in self._resident:
            return {"allowed": True, "already_resident": True, "engine": engine,
                    "model": model, "est_gb": est_gb, "route": "load", "queued": False,
                    "reasons": []}
        m = self._machine_fn()
        verdict = evaluate_budget(m["total_gb"], m["available_gb"], self.resident_gb(),
                                   est_gb, self._replace_gb(engine, model),
                                   self._budget_frac, self._free_floor_frac)
        decision = {"allowed": verdict["allowed"], "engine": engine, "model": model,
                    "est_gb": est_gb, "source": source,
                    "route": "load" if verdict["allowed"] else "queue",
                    "queued": not verdict["allowed"], "reasons": verdict["reasons"]}
        if verdict["allowed"]:
            self._pending[key] = time.time()
            self._resident[key] = {"engine": engine, "model": model, "est_gb": est_gb,
                                   "observed_gb": None, "managed": True,
                                   "loaded_at": time.time(), "last_used_at": time.time()}
            self._append({"kind": "load_allowed", "engine": engine, "model": model,
                          "est_gb": est_gb, "managed": True, "source": source})
            self._queue_clear(engine, model)
        else:
            last = getattr(self, "_last_quick_refusal", {})
            if time.time() - last.get(key, 0) > 60:
                last[key] = time.time()
                self._last_quick_refusal = last
                self._append({"kind": "load_refused", "engine": engine, "model": model,
                              "est_gb": est_gb, "managed": True, "source": source,
                              "route": "queue", "reasons": verdict["reasons"]})
                self._queue_add(decision)
        return decision

    # ------------------------------------------------------------ load/unload records

    def note_loaded(self, engine: str, model: str, observed_gb: Optional[float] = None,
                    source: str = "") -> None:
        """A load Xod initiated has landed: record it, with the observed footprint
        when the caller knows it (reconcile fills it in otherwise)."""
        self._ensure_state()
        key = f"{engine}|{model}"
        prev = self._resident.get(key, {})
        self._resident[key] = {"engine": engine, "model": model,
                               "est_gb": prev.get("est_gb"), "observed_gb": observed_gb,
                               "managed": True, "loaded_at": time.time(),
                               "last_used_at": time.time()}
        self._pending.pop(key, None)
        self._append({"kind": "loaded", "engine": engine, "model": model,
                      "est_gb": prev.get("est_gb"), "observed_gb": observed_gb,
                      "managed": True, "source": source})
        self._queue_clear(engine, model)

    def note_load_failed(self, engine: str, model: str, source: str = "") -> None:
        """An approved load that did not land: drop the intent so a phantom model
        never holds budget, and say so in the ledger."""
        self._ensure_state()
        key = f"{engine}|{model}"
        if key in self._pending or key in self._resident:
            self._pending.pop(key, None)
            self._resident.pop(key, None)
            self._append({"kind": "load_failed", "engine": engine, "model": model,
                          "managed": True, "source": source})

    def note_unloaded(self, engine: str, model: str, reason: str, source: str = "") -> None:
        self._ensure_state()
        entry = self._resident.pop(f"{engine}|{model}", None)
        self._pending.pop(f"{engine}|{model}", None)
        if entry is not None:
            self._append({"kind": "unloaded", "engine": engine, "model": model,
                          "observed_gb": entry.get("observed_gb"),
                          "est_gb": entry.get("est_gb"), "managed": entry["managed"],
                          "reason": reason, "source": source})

    def note_used(self, engine: str, model: str) -> None:
        self._ensure_state()
        entry = self._resident.get(f"{engine}|{model}")
        if entry is not None:
            entry["last_used_at"] = time.time()

    # ------------------------------------------------------------ idle + pressure unload

    def _idle(self, entry: Dict[str, Any], answering: Dict[str, Any], idle_sec: float) -> bool:
        if answering.get("provider") == entry["engine"]:
            return False                      # it is (an) answering engine: not idle
        last = entry.get("last_used_at") or entry.get("loaded_at") or 0
        return time.time() - last >= idle_sec

    async def pressure_unload(self) -> Dict[str, Any]:
        """Memory pressure: unload idle Xod-managed models now, largest first.
        Models Xod did not load are never unloaded from here; they come back as
        suggestions for a Tier 2 approval (Computer Health surfaces them)."""
        if not _enabled():
            return {"actions": [], "suggestions": []}
        snap = await self.reconcile()
        answering = snap.get("answering") or {}
        managed = [e for e in self.resident() if e["managed"]]
        idle = [e for e in managed if self._idle(e, answering, PRESSURE_IDLE_SEC)]
        idle.sort(key=lambda e: e["gb"], reverse=True)
        actions = []
        for e in idle:
            ok = await self._unloder(e["engine"], e["model"])
            if ok:
                self.note_unloaded(e["engine"], e["model"], "memory_pressure", "pressure_unload")
            actions.append({"action": "pressure_unload", "engine": e["engine"],
                            "model": e["model"], "gb": e["gb"], "ok": bool(ok)})
        suggestions = [{"engine": e["engine"], "model": e["model"], "gb": e["gb"],
                        "note": "Loaded outside Xod; Xod does not unload it. "
                                "Freeing it is the owner's call."}
                       for e in self.resident() if not e["managed"]]
        if suggestions:
            self._append({"kind": "pressure_suggestion", "suggestions": suggestions,
                          "source": "pressure_unload"})
        return {"actions": actions, "suggestions": suggestions}

    async def check_idle_unloads(self, grace_minutes: int) -> List[Dict[str, Any]]:
        """The Oct 31 grace timer, cross-engine: models XOD loaded into LM Studio /
        Ollama are unloaded once unused for the grace period while the answering
        provider is elsewhere. (The built-in engine's own idle stop lives in
        local_servers.check_idle; user-started engines' own models are never
        touched, only models Xod itself loaded.)"""
        if not grace_minutes or not _enabled():
            return []
        snap = await self.reconcile()
        answering = snap.get("answering") or {}
        actions = []
        for e in list(self.resident()):
            if e["engine"] == "builtin" or not e["managed"]:
                continue
            if not self._idle(e, answering, grace_minutes * 60):
                continue
            ok = await self._unloder(e["engine"], e["model"])
            if ok:
                self.note_unloaded(e["engine"], e["model"], "idle", "idle_unload")
            actions.append({"action": "idle_unload", "engine": e["engine"],
                            "model": e["model"], "gb": e["gb"], "ok": bool(ok)})
        return actions

    async def on_pressure(self, findings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Computer Health hook: a memory_pressure finding triggers the unload."""
        if not any(f.get("rule") == "memory_pressure" for f in findings or []):
            return None
        try:
            return await self.pressure_unload()
        except Exception as e:
            logger.warning(f"pressure unload failed: {e}")
            return None


def _env_frac(name: str, default: float) -> float:
    try:
        return float(os.environ[name]) if os.environ.get(name) else default
    except ValueError:
        return default


def _enabled() -> bool:
    return os.environ.get("XOD_MODEL_BUDGET", "on").strip().lower() != "off"


model_budget = ModelBudget(
    budget_frac=_env_frac("XOD_MODEL_BUDGET_FRAC", BUDGET_FRAC),
    free_floor_frac=_env_frac("XOD_MODEL_FREE_FLOOR_FRAC", FREE_FLOOR_FRAC))


def budget_status() -> Dict[str, Any]:
    return model_budget.budget_status()
