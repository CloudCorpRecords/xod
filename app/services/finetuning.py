"""
On-device training for Xod's specialist mini models (MLX LoRA on Apple Silicon).

Flow for a specialist:
  1. build_dataset   - good teacher examples -> train / valid / test JSONL (deduped, validated)
  2. start_training  - mlx_lm.lora in a subprocess, live loss parsing, sized to the data
  3. evaluate        - the trained student answers the held-out test set; scored against the
                       teacher (valid JSON, same action). Passing students can be switched on.
Jobs are saved to data/models/jobs.json so the history survives restarts.

Two kinds of specialists are supported:
  - example-based (app/services/specialists.py): learned from tagged LLM calls
  - trace-based   (trace_recorder.SPECIALIST_TARGETS): the older intent router etc.
"""
import asyncio
import json
import math
import os
import sys
import random
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.paths import DATA_DIR

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TRAINING_DIR = DATA_DIR / "training"
MODELS_DIR = DATA_DIR / "models"
JOBS_PATH = MODELS_DIR / "jobs.json"
VENV_PYTHON = Path(sys.executable)   # same Python as the server

# Default student for trace-based specialists (tiny prompts).
DEFAULT_STUDENT_MODEL = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"
# Memory safety (a 16 GB Mac froze when training ran next to the 12B teacher):
MIN_FREE_GB_DURING_TRAINING = 1.0   # watchdog stops training below this
MAX_EXTRA_SWAP_GB = 2.0             # ...or if swap grows this much
LMS_CLI = Path.home() / ".lmstudio" / "bin" / "lms"

STUDENT_CHOICES = [
    "mlx-community/Qwen2.5-0.5B-Instruct-4bit",
    "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
    "mlx-community/Qwen2.5-3B-Instruct-4bit",
]


def _normalize_json_response(text: str) -> Optional[str]:
    """Teacher replies sometimes wrap JSON in prose or ``` fences; students learn the clean JSON."""
    from app.services.llm import parse_llm_json
    obj = parse_llm_json(text)
    if not obj:
        return None
    obj.pop("think", None)
    return json.dumps(obj, ensure_ascii=False)


def _validate(validator: str, response: str) -> Optional[str]:
    if validator == "json_action":
        out = _normalize_json_response(response)
        if not out or not json.loads(out).get("action"):
            return None
        from app.services.specialists import clean_action
        return json.dumps(clean_action(json.loads(out)), ensure_ascii=False)
    if validator == "json_decision":
        out = _normalize_json_response(response)
        return out if out and json.loads(out).get("decision") in ("direct_answer", "execute_plan") else None
    return response.strip() or None


def _approx_tokens(text: str) -> int:
    return len(text) // 3 + 1


def _model_size_gb(model_id: str) -> float:
    """Size of a downloaded model in the Hugging Face cache (0 if not downloaded yet)."""
    cache = Path.home() / ".cache" / "huggingface" / "hub" / ("models--" + model_id.replace("/", "--"))
    if not cache.exists():
        return 0.0
    return sum(f.stat().st_size for f in cache.rglob("*") if f.is_file() and not f.is_symlink()) / 1e9 or \
        sum(f.resolve().stat().st_size for f in cache.rglob("*.safetensors")) / 1e9


def estimate_training_gb(model_id: str, max_seq: int) -> float:
    """Rough peak memory for LoRA training with gradient checkpointing (deliberately on the safe side)."""
    weights = _model_size_gb(model_id) or (2.0 if "3B" in model_id else 1.0 if "1.5B" in model_id else 0.4)
    params_b = 3.0 if "3B" in model_id else 1.5 if "1.5B" in model_id else 0.5
    return round(1.0 + weights * 1.5 + params_b * 0.7 * (max_seq / 1024), 1)


def memory_snapshot() -> Dict[str, float]:
    import psutil
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    return {"available_gb": round(vm.available / 1e9, 2), "total_gb": round(vm.total / 1e9, 1),
            "swap_used_gb": round(sw.used / 1e9, 2)}


class FineTuningService:
    def __init__(self):
        TRAINING_DIR.mkdir(parents=True, exist_ok=True)
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        self.active_jobs: Dict[str, Dict[str, Any]] = {}
        self._procs: Dict[str, Any] = {}
        self._load_jobs()

    # ------------------------------------------------------------------ jobs on disk
    def _load_jobs(self) -> None:
        try:
            for job in json.loads(JOBS_PATH.read_text()):
                if job.get("status") in ("training", "evaluating", "preparing"):
                    job["status"] = "interrupted"  # the app restarted mid-run
                self.active_jobs[job["id"]] = job
        except Exception:
            pass

    def _save_jobs(self) -> None:
        try:
            keep = sorted(self.active_jobs.values(), key=lambda j: j.get("start_time", 0))[-50:]
            JOBS_PATH.write_text(json.dumps([{k: v for k, v in j.items() if k != "logs"} | {"logs": j.get("logs", [])[-40:]}
                                             for j in keep], indent=2))
        except Exception:
            pass

    # ------------------------------------------------------------------ datasets
    def build_dataset(self, specialist: str, val_ratio: float = 0.1, test_ratio: float = 0.1) -> Dict[str, Any]:
        """Writes train/valid/test.jsonl for a specialist. Test examples are never trained on."""
        from app.services.specialists import SPECIALISTS
        if specialist in SPECIALISTS:
            spec = SPECIALISTS[specialist]
            from app.services import dataset_recorder
            raw = dataset_recorder.fetch_training_examples(spec["task"], min_label=spec.get("min_label", 1))
            lines, too_long = [], 0
            cap = spec.get("max_seq_length", 4096)
            for ex in raw:
                target = _validate(spec.get("validator", ""), ex["response"])
                if not target:
                    continue
                msgs = ex["messages"] + [{"role": "assistant", "content": target}]
                # Training cuts long sequences from the end, which is where the answer is.
                # Drop them rather than teach the student from a cut-off answer.
                if sum(_approx_tokens(m["content"]) for m in msgs) + 50 > cap:
                    too_long += 1
                    continue
                lines.append(json.dumps({"messages": msgs}, ensure_ascii=False))
            skipped = len(raw) - len(lines)
        else:
            from app.services.trace_recorder import export_training_data
            export_res = export_training_data(specialist=specialist)
            if not export_res.get("success"):
                return export_res
            lines = [l.strip() for l in Path(export_res["path"]).read_text(encoding="utf-8").splitlines() if l.strip()]
            skipped = 0

        longest = max((sum(_approx_tokens(m["content"]) for m in json.loads(l)["messages"]) for l in lines), default=0)
        if len(lines) < 4:
            return {"success": False, "error": f"Only {len(lines)} usable examples for '{specialist}'. Keep using Xod to collect more."}

        random.Random(42).shuffle(lines)
        n_test = max(1, int(len(lines) * test_ratio))
        n_val = max(1, int(len(lines) * val_ratio))
        test, valid, train = lines[:n_test], lines[n_test:n_test + n_val], lines[n_test + n_val:]

        dataset_dir = TRAINING_DIR / specialist
        dataset_dir.mkdir(parents=True, exist_ok=True)
        for name, rows in (("train", train), ("valid", valid), ("test", test)):
            (dataset_dir / f"{name}.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
        return {
            "success": True, "specialist": specialist, "dataset_dir": str(dataset_dir),
            "train_examples": len(train), "valid_examples": len(valid), "test_examples": len(test),
            "skipped_invalid": skipped, "dropped_too_long": locals().get("too_long", 0),
            # Train at the length the data needs (multiple of 256), not a fixed maximum: far less memory.
            "max_seq_length": int(min(8192, max(1024, math.ceil((longest + 64) / 256) * 256))),
        }

    # Kept for older callers.
    def prepare_dataset(self, specialist: str = "intent_router", val_ratio: float = 0.2) -> Dict[str, Any]:
        return self.build_dataset(specialist, val_ratio=val_ratio)

    # ------------------------------------------------------------------ training
    async def start_training_job(
        self,
        specialist: str = "intent_router",
        base_model: Optional[str] = None,
        iters: Optional[int] = None,
        batch_size: int = 1,
        learning_rate: float = 1e-4,
        num_layers: int = 8,
        epochs: float = 3.0,
    ) -> Dict[str, Any]:
        """Builds the dataset and launches MLX LoRA training in the background."""
        from app.services.specialists import SPECIALISTS
        if any(j["status"] in ("training", "evaluating", "preparing") for j in self.active_jobs.values()):
            return {"success": False, "error": "Another training run is in progress. One at a time keeps your Mac responsive."}
        spec = SPECIALISTS.get(specialist, {})
        base_model = base_model or spec.get("base_model") or DEFAULT_STUDENT_MODEL

        prep = self.build_dataset(specialist)
        if not prep.get("success"):
            return prep
        max_seq = prep["max_seq_length"]

        # Memory pre-flight. The teacher model is paused (unloaded) while training so both don't fight for RAM.
        need_gb = estimate_training_gb(base_model, max_seq)
        teacher = await self._pause_teacher()
        await asyncio.sleep(2)
        mem = memory_snapshot()
        if mem["available_gb"] < need_gb + MIN_FREE_GB_DURING_TRAINING:
            await self._resume_teacher(teacher)
            return {"success": False, "error": (
                f"Not enough free memory to train safely: about {need_gb} GB needed, {mem['available_gb']} GB free "
                f"(after pausing Xod's main model). Close some apps, or pick a smaller student model.")}
        # Size the run to the data: ~3 passes over the training set, within sane bounds.
        if not iters:
            iters = int(min(1500, max(60, math.ceil(epochs * prep["train_examples"] / batch_size))))

        job_id = f"ft-{uuid.uuid4().hex[:8]}"
        adapter_dir = MODELS_DIR / specialist / job_id
        adapter_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            str(VENV_PYTHON), "-m", "app.services.mlx_train",  # mlx_lm.lora with memory caps
            "--model", base_model, "--train",
            "--data", prep["dataset_dir"],
            "--adapter-path", str(adapter_dir),
            "--batch-size", str(batch_size),
            "--iters", str(iters),
            "--learning-rate", str(learning_rate),
            "--num-layers", str(num_layers),
            "--max-seq-length", str(max_seq),
            "--steps-per-report", "5",
            "--steps-per-eval", str(max(25, iters // 6)),
            "--val-batches", "10",
            "--save-every", str(max(50, iters // 4)),
            "--grad-checkpoint",
            "--mask-prompt",
        ]
        try:
            # Cap MLX: a small buffer cache, and a hard budget that leaves headroom for the rest of the Mac.
            budget_gb = max(need_gb, mem["available_gb"] - 2.0 * MIN_FREE_GB_DURING_TRAINING)
            env = dict(os.environ, XOD_MLX_CACHE_GB="0.5", XOD_MLX_MEMORY_GB=f"{budget_gb:.1f}")
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(BASE_DIR), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,  # one stream: no pipe deadlock
            )
        except Exception as e:
            return {"success": False, "error": f"Could not start MLX training: {e}"}

        job: Dict[str, Any] = {
            "id": job_id, "specialist": specialist, "base_model": base_model,
            "adapter_dir": str(adapter_dir), "dataset_dir": prep["dataset_dir"],
            "train_examples": prep["train_examples"], "test_examples": prep["test_examples"],
            "total_iters": iters, "current_iter": 0, "loss": None, "val_loss": None,
            "loss_curve": [], "status": "training",
            "start_time": time.time(), "start_time_str": time.strftime("%Y-%m-%d %H:%M"),
            "pid": proc.pid, "logs": [], "max_seq_length": max_seq,
            "memory": {"estimated_gb": need_gb, "available_at_start_gb": mem["available_gb"]},
            "teacher_paused": teacher,
        }
        self.active_jobs[job_id] = job
        self._procs[job_id] = proc
        self._save_jobs()
        asyncio.create_task(self._monitor(job, proc))
        asyncio.create_task(self._memory_watchdog(job, proc, mem["swap_used_gb"]))
        return {"success": True, "job_id": job_id, "specialist": specialist, "base_model": base_model,
                "dataset_examples": prep["train_examples"], "test_examples": prep["test_examples"],
                "total_iters": iters, "status": "training"}

    async def start_preference_job(self, specialist: str, max_corrections: int = 25) -> Dict[str, Any]:
        """
        "Learn from mistakes": continues the current student with preference training (DPO) on
        (right, wrong) pairs from teacher corrections, rule fixes, and the student's own mistakes.
        Graded on the same held-out test as the student it starts from; kept only if it's better.
        """
        from app.services.specialists import SPECIALISTS, load_state
        from app.services import preference_data
        if any(j["status"] in ("training", "evaluating", "preparing") for j in self.active_jobs.values()):
            return {"success": False, "error": "Another training run is in progress."}
        spec = SPECIALISTS.get(specialist)
        entry = load_state().get(specialist) or {}
        dataset_dir = TRAINING_DIR / specialist
        if not spec or not entry.get("adapter_dir") or not (dataset_dir / "train.jsonl").exists():
            return {"success": False, "error": "Train this model once first; learning from mistakes builds on it."}
        stats = preference_data.pair_stats(spec["task"])
        # Student mistakes are mined at training time (roughly a third of practice prompts), so they count toward the bar.
        expected = stats["total"] + stats["uncorrected_failures"] + len((dataset_dir / "train.jsonl").read_text().splitlines()) // 3
        if expected < preference_data.MIN_LESSONS:
            return {"success": False, "error": (
                f"Not enough lessons yet: about {expected} of {preference_data.MIN_LESSONS}. With fewer, practice makes the "
                "model memorize and get worse. Keep using Xod; this unlocks on its own.")}
        init_cfg = json.loads((Path(entry["adapter_dir"]) / "adapter_config.json").read_text())
        max_seq = int(init_cfg.get("max_seq_length") or 3072)

        job_id = f"pt-{uuid.uuid4().hex[:8]}"
        adapter_dir = MODELS_DIR / specialist / job_id
        adapter_dir.mkdir(parents=True, exist_ok=True)
        job: Dict[str, Any] = {
            "id": job_id, "specialist": specialist, "method": "dpo", "base_model": entry["base_model"],
            "init_adapter": entry["adapter_dir"], "adapter_dir": str(adapter_dir), "dataset_dir": str(dataset_dir),
            "baseline_eval": entry.get("eval"), "status": "preparing", "total_iters": 0, "current_iter": 0,
            "loss": None, "val_loss": None, "loss_curve": [], "logs": [], "max_seq_length": max_seq,
            "start_time": time.time(), "start_time_str": time.strftime("%Y-%m-%d %H:%M"),
            "memory": {}, "train_examples": None,
        }
        self.active_jobs[job_id] = job
        self._save_jobs()
        asyncio.create_task(self._run_preference_job(job, spec, max_corrections))
        return {"success": True, "job_id": job_id, "specialist": specialist, "status": "preparing"}

    async def _run_preference_job(self, job: Dict[str, Any], spec: Dict[str, Any], max_corrections: int) -> None:
        from app.services import preference_data
        try:
            # 1. Corrections need the teacher, so they happen before it's paused.
            job["logs"].append("Asking the main model to correct recent mistakes…")
            made = await preference_data.build_pairs(spec["task"], limit=max_corrections)
            job["pairs_made"] = made
            pairs_path = Path(job["dataset_dir"]) / "pairs.jsonl"
            job["pairs_total"] = preference_data.export_pairs(spec["task"], pairs_path)

            # 2. Same memory safety as regular training.
            need_gb = estimate_training_gb(job["base_model"], job["max_seq_length"])
            teacher = await self._pause_teacher()
            job["teacher_paused"] = teacher
            await asyncio.sleep(2)
            mem = memory_snapshot()
            job["memory"] = {"estimated_gb": need_gb, "available_at_start_gb": mem["available_gb"]}
            if mem["available_gb"] < need_gb + MIN_FREE_GB_DURING_TRAINING:
                job["status"], job["stop_reason"] = "stopped", f"Not enough free memory ({mem['available_gb']} GB)."
                await self._resume_teacher(teacher)
                self._save_jobs()
                return

            budget_gb = max(need_gb, mem["available_gb"] - 2.0 * MIN_FREE_GB_DURING_TRAINING)
            env = dict(os.environ, XOD_MLX_CACHE_GB="0.5", XOD_MLX_MEMORY_GB=f"{budget_gb:.1f}")
            proc = await asyncio.create_subprocess_exec(
                str(VENV_PYTHON), "-m", "app.services.mlx_dpo",
                "--model", job["base_model"], "--init-adapter", job["init_adapter"],
                "--pairs", str(pairs_path), "--mine", str(Path(job["dataset_dir"]) / "train.jsonl"),
                "--adapter-path", job["adapter_dir"], "--max-seq-length", str(job["max_seq_length"]),
                cwd=str(BASE_DIR), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            job["status"], job["pid"] = "training", proc.pid
            self._procs[job["id"]] = proc
            self._save_jobs()
            asyncio.create_task(self._memory_watchdog(job, proc, mem["swap_used_gb"]))
            await self._monitor(job, proc)
        except Exception as e:
            job["status"], job["stop_reason"] = "failed", str(e)[:300]
            await self._resume_teacher(job.get("teacher_paused"))
            self._save_jobs()

    async def _monitor(self, job: Dict[str, Any], proc) -> None:
        last_save = time.time()
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            job["logs"].append(text)
            del job["logs"][:-300]
            # "Iter 10: Train loss 1.234, ..." / "Iter 25: Val loss 1.120, ..."
            if text.startswith("Pairs:"):
                try:  # "Pairs: 40 train, 7 validation, ..." -> two passes over the training pairs
                    n_train = int(text.split()[1])
                    job["train_examples"], job["total_iters"] = n_train, max(20, int(1.5 * n_train))
                except Exception:
                    pass
            if text.startswith(("Mined", "Before:", "After:")):
                job.setdefault("preference_notes", []).append(text)
            if "Not enough usable pairs" in text:
                job["stop_reason"] = "Not enough mistakes to learn from yet. Keep using Xod, then try again."
            if text.startswith("Iter"):
                try:
                    it = int(text.split()[1].rstrip(":"))
                    job["current_iter"] = it
                    if "Train loss" in text:
                        job["loss"] = float(text.split("Train loss")[1].split(",")[0])
                        job["loss_curve"].append([it, job["loss"]])
                    if "Val loss" in text:
                        job["val_loss"] = float(text.split("Val loss")[1].split(",")[0])
                    if "Val pref acc" in text:
                        job["val_pref_acc"] = float(text.split("Val pref acc")[1].split(",")[0])
                    if "Peak mem" in text:
                        job["memory"]["peak_gb"] = float(text.split("Peak mem")[1].split("GB")[0])
                except Exception:
                    pass
            if time.time() - last_save > 10:
                self._save_jobs()
                last_save = time.time()
        await proc.wait()
        self._procs.pop(job["id"], None)
        job["duration_sec"] = round(time.time() - job["start_time"], 1)
        weights = Path(job["adapter_dir"]) / "adapters.safetensors"
        if proc.returncode == 0 and weights.exists():
            job["weights_ready"] = True
            job["status"] = "evaluating"
            self._save_jobs()
            await self.evaluate(job["id"])
        else:
            job["status"] = job.get("stop_reason") and "stopped" or ("cancelled" if job.get("cancelled") else "failed")
            job["exit_code"] = proc.returncode
        await self._resume_teacher(job.get("teacher_paused"))
        self._save_jobs()

    async def _memory_watchdog(self, job: Dict[str, Any], proc, swap_start_gb: float) -> None:
        """Stops training before the Mac runs out of memory (a frozen Mac is much worse than a stopped run)."""
        while proc.returncode is None:
            await asyncio.sleep(3)
            try:
                mem = memory_snapshot()
            except Exception:
                continue
            job["memory"]["available_now_gb"] = mem["available_gb"]
            low = mem["available_gb"] < MIN_FREE_GB_DURING_TRAINING
            swapping = mem["swap_used_gb"] - swap_start_gb > MAX_EXTRA_SWAP_GB
            if (low or swapping) and proc.returncode is None:
                job["stop_reason"] = (f"Stopped to protect your Mac: only {mem['available_gb']} GB free" if low
                                      else f"Stopped to protect your Mac: swap grew by {mem['swap_used_gb'] - swap_start_gb:.1f} GB")
                job["logs"].append(job["stop_reason"])
                proc.terminate()
                return

    async def _pause_teacher(self) -> Optional[Dict[str, Any]]:
        """Unloads the LM Studio model during training; returns what to reload afterwards."""
        try:
            from app.config import settings
            if settings.llm_provider != "lm_studio":
                return None
            from app.services.llm import llm_service
            status = await llm_service.get_lmstudio_status()
            loaded = status.get("loaded_model")
            if not loaded:
                return None
            await llm_service.unload_lmstudio_model(loaded.get("instance_id"))
            return {"key": loaded["key"], "context_length": loaded.get("context_length")}
        except Exception:
            return None

    async def _resume_teacher(self, teacher: Optional[Dict[str, Any]]) -> None:
        if not teacher:
            return
        try:
            if LMS_CLI.exists():
                args = [str(LMS_CLI), "load", teacher["key"], "-y"]
                if teacher.get("context_length"):
                    args += ["--context-length", str(teacher["context_length"])]
                p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await p.wait()
                if p.returncode == 0:
                    return
            from app.services.llm import llm_service
            await llm_service.load_lmstudio_model(teacher["key"])
        except Exception:
            pass

    async def cancel(self, job_id: str) -> Dict[str, Any]:
        proc = self._procs.get(job_id)
        if not proc:
            return {"success": False, "error": "That job isn't running."}
        self.active_jobs[job_id]["cancelled"] = True
        proc.terminate()
        return {"success": True}

    # ------------------------------------------------------------------ evaluation
    async def evaluate(self, job_id: str) -> Dict[str, Any]:
        """Scores the trained student on held-out teacher examples and records whether it can be switched on."""
        from app.services import specialists
        job = self.active_jobs.get(job_id)
        if not job:
            return {"success": False, "error": "unknown job"}
        spec = specialists.SPECIALISTS.get(job["specialist"], {})
        job["status"] = "evaluating"
        test_file = Path(job["dataset_dir"]) / "test.jsonl"
        if job.get("method") == "dpo":
            # Preference runs are compared with the student they started from, on a bigger shared set
            # (test + validation; neither is ever trained on), so both are graded on the same questions.
            test_file = Path(job["dataset_dir"]) / "grade.jsonl"
            rows = [l for name in ("test.jsonl", "valid.jsonl")
                    for l in (Path(job["dataset_dir"]) / name).read_text().splitlines() if l.strip()]
            test_file.write_text("\n".join(rows) + "\n")
            base = await self._run_eval(job["base_model"], job["init_adapter"], test_file,
                                        spec.get("validator", "text"), Path(job["adapter_dir"]) / "baseline_eval.json")
            if base.get("error"):
                job["status"], job["eval"] = "completed", base
                self._save_jobs()
                return {"success": False, "error": "evaluation failed", "details": base["error"]}
            job["baseline_eval"] = base
        result = await self._run_eval(job["base_model"], job["adapter_dir"], test_file,
                                      spec.get("validator", "text"), Path(job["adapter_dir"]) / "eval.json")
        if result.get("error"):
            job["status"] = "completed"
            job["eval"] = result
            self._save_jobs()
            return {"success": False, "error": "evaluation failed", "details": result["error"]}

        result["promotable"] = (
            result.get("valid_json_rate", 0) >= specialists.PROMOTE_MIN_VALID_JSON
            and result.get("action_match_rate", 0) >= specialists.PROMOTE_MIN_ACTION_MATCH
        ) if spec else False
        job["eval"] = result
        job["status"] = "completed"
        self._save_jobs()

        # Example-based specialists: remember the newest trained version and its test result.
        if spec and job.get("method") == "dpo":
            base = job.get("baseline_eval") or {}
            job["improvement"] = round(result.get("action_match_rate", 0) - base.get("action_match_rate", 0), 3)
            if job["improvement"] < 0 or (job["improvement"] == 0 and result.get("valid_json_rate", 0) < base.get("valid_json_rate", 0)):
                job["kept_previous"] = True  # not better on the same test: the earlier student stays
                self._save_jobs()
                return {"success": True, "eval": result, "kept_previous": True}
        if spec:
            state = specialists.load_state()
            entry = state.get(job["specialist"]) or {}
            was_enabled = entry.get("enabled")
            entry.update({"job_id": job_id, "adapter_dir": job["adapter_dir"], "base_model": job["base_model"],
                          "eval": result, "trained_at": time.strftime("%Y-%m-%d %H:%M")})
            if was_enabled and not result["promotable"]:
                entry["enabled"] = False  # a worse retrain never silently replaces a working one
            state[job["specialist"]] = entry
            specialists.save_state(state)
            if was_enabled and result["promotable"]:
                await specialists.start_server(job["specialist"])  # hot-swap to the new adapter
        return {"success": True, "eval": result}

    async def _run_eval(self, base_model: str, adapter_dir: str, test_file: Path, validator: str, out_path: Path) -> Dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            str(VENV_PYTHON), "-m", "app.services.specialist_eval",
            "--model", base_model, "--adapter", str(adapter_dir), "--test", str(test_file),
            "--validator", validator, "--out", str(out_path),
            cwd=str(BASE_DIR), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await proc.communicate()
        try:
            return json.loads(out_path.read_text())
        except Exception:
            return {"error": output.decode("utf-8", "replace")[-500:]}

    # ------------------------------------------------------------------ views
    def get_job_status(self, job_id: str) -> Dict[str, Any]:
        job = self.active_jobs.get(job_id)
        if not job:
            return {"success": False, "error": f"Job not found: {job_id}"}
        view = {k: v for k, v in job.items() if k not in ("logs", "pid")}
        view["logs_tail"] = job.get("logs", [])[-20:]
        view["duration_sec"] = job.get("duration_sec", round(time.time() - job["start_time"], 1))
        return {"success": True, "job": view}

    def list_jobs(self) -> List[Dict[str, Any]]:
        jobs = sorted(self.active_jobs.values(), key=lambda j: j.get("start_time", 0), reverse=True)
        return [
            {k: j.get(k) for k in ("id", "specialist", "base_model", "status", "current_iter", "total_iters",
                                    "loss", "val_loss", "start_time_str", "train_examples", "duration_sec", "stop_reason",
                                    "method", "improvement", "kept_previous", "pairs_total", "baseline_eval")}
            | {"eval": j.get("eval")}
            for j in jobs
        ]


finetuning_service = FineTuningService()
