"""
mission_checkpoint: persisted mid-flight checklist / task-graph state for long-horizon
browser and desktop missions (e.g. 20-step job applications or multi-app workflows).

Each mission stores its ordered steps, status (pending / in_progress / done / failed),
and evidence under DATA_DIR/mission_checkpoints/<mission_id>.json so:
  - the LLM prompt always sees an accurate compact checklist instead of drifting, and
  - a stalled or interrupted run can resume from the exact next pending/failed step.
"""
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _dir() -> Path:
    from app.paths import DATA_DIR
    d = DATA_DIR / "mission_checkpoints"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_id(mission_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(mission_id or "mission")).strip("_")[:80] or "mission"


class MissionChecklist:
    """Persisted step-level state machine for a single browser or desktop mission."""

    def __init__(self, mission_id: str, goal: str = "", steps: Optional[List[Dict[str, Any]]] = None):
        self.mission_id = _safe_id(mission_id)
        self.goal = str(goal or "")
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.steps: List[Dict[str, Any]] = []
        for idx, s in enumerate(steps or []):
            if isinstance(s, str):
                self.steps.append({"id": idx + 1, "title": s, "status": "pending", "evidence": "", "error": ""})
            elif isinstance(s, dict):
                self.steps.append({
                    "id": int(s.get("id", idx + 1)),
                    "title": str(s.get("title") or s.get("action") or f"Step {idx + 1}"),
                    "status": str(s.get("status") or "pending"),
                    "evidence": str(s.get("evidence") or ""),
                    "error": str(s.get("error") or ""),
                })

    @property
    def path(self) -> Path:
        return _dir() / f"{self.mission_id}.json"

    def save(self) -> Path:
        self.updated_at = time.time()
        payload = {
            "mission_id": self.mission_id,
            "goal": self.goal,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "steps": self.steps,
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return self.path

    @classmethod
    def load(cls, mission_id: str) -> Optional["MissionChecklist"]:
        p = _dir() / f"{_safe_id(mission_id)}.json"
        if not p.is_file():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        mc = cls(raw.get("mission_id") or mission_id, raw.get("goal") or "", raw.get("steps") or [])
        mc.created_at = float(raw.get("created_at") or mc.created_at)
        mc.updated_at = float(raw.get("updated_at") or mc.updated_at)
        return mc

    def _find(self, step_id: int) -> Optional[Dict[str, Any]]:
        return next((s for s in self.steps if s["id"] == int(step_id)), None)

    def mark_in_progress(self, step_id: int) -> None:
        s = self._find(step_id)
        if s is not None:
            s["status"] = "in_progress"
            s["error"] = ""
            self.save()

    def mark_done(self, step_id: int, evidence: str = "") -> None:
        s = self._find(step_id)
        if s is not None:
            s["status"] = "done"
            s["evidence"] = str(evidence or "")[:200]
            s["error"] = ""
            self.save()

    def mark_failed(self, step_id: int, error: str = "") -> None:
        s = self._find(step_id)
        if s is not None:
            s["status"] = "failed"
            s["error"] = str(error or "")[:200]
            self.save()

    def next_pending(self) -> Optional[Dict[str, Any]]:
        for s in self.steps:
            if s["status"] in ("pending", "in_progress", "failed"):
                return s
        return None

    def resume_index(self) -> int:
        """0-based index of the first unfinished step (len(steps) if all done)."""
        for i, s in enumerate(self.steps):
            if s["status"] != "done":
                return i
        return len(self.steps)

    def progress(self) -> Dict[str, int]:
        done = sum(1 for s in self.steps if s["status"] == "done")
        failed = sum(1 for s in self.steps if s["status"] == "failed")
        return {"total": len(self.steps), "done": done, "failed": failed, "remaining": len(self.steps) - done}

    def render_prompt_block(self) -> str:
        """Compact checklist string for injecting into LLM prompts."""
        icons = {"done": "[✓]", "in_progress": "[→]", "failed": "[✗]", "pending": "[ ]"}
        lines = [f"MISSION CHECKLIST ({self.progress()['done']}/{len(self.steps)} done): {self.goal}"]
        for s in self.steps:
            icon = icons.get(s["status"], "[ ]")
            tail = f" — {s['evidence']}" if s.get("evidence") else (f" — ERR: {s['error']}" if s.get("error") else "")
            lines.append(f"  {icon} {s['id']}. {s['title']}{tail}")
        return "\n".join(lines)
