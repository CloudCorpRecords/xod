"""
Multi-browser concurrent pool for Xod (2, 3, 4+ independent browsers in parallel).

Each session gets its own:
  - BrowserSandboxService instance
  - isolated Chrome profile directory under <XOD_DATA_DIR>/browser_sessions/<session_id>
  - distinct free CDP port (never collides with port 9228 or another session)
  - independent asyncio.Lock and tab claim

This allows Xod (or an external MCP steerer) to drive multiple browsers concurrently
at equivalent throughput without lock contention or tab/cookie cross-contamination.
"""
import asyncio
import re
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.paths import DATA_DIR as XOD_DATA_DIR
from app.services.browser_sandbox import BrowserSandboxService, DEBUG_PORT

MAX_CONCURRENT_BROWSERS = 8


def _safe_session_id(session_id: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]", "_", (session_id or "worker").strip())[:48]
    return clean or "worker"


def _find_free_port(start_port: int, reserved: set) -> int:
    for port in range(start_port, start_port + 200):
        if port in reserved or port == DEBUG_PORT:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local CDP port available for new browser session.")


class BrowserPoolService:
    def __init__(self, base_data_dir: Optional[Path] = None):
        self._sessions: Dict[str, BrowserSandboxService] = {}
        self._reserved_ports: set = set()
        self._base_dir: Path = Path(base_data_dir) if base_data_dir else (Path(XOD_DATA_DIR) / "browser_sessions")

    def get_or_create(self, session_id: str, headless: bool = True) -> BrowserSandboxService:
        sid = _safe_session_id(session_id)
        if sid in self._sessions:
            return self._sessions[sid]
        if len(self._sessions) >= MAX_CONCURRENT_BROWSERS:
            raise RuntimeError(f"Browser pool cap reached ({MAX_CONCURRENT_BROWSERS} concurrent browsers). Close an idle session first.")
        port = _find_free_port(DEBUG_PORT + 10, self._reserved_ports)
        profile_dir = self._base_dir / sid
        profile_dir.mkdir(parents=True, exist_ok=True)
        sb = BrowserSandboxService(port=port, data_dir=str(profile_dir), session_id=sid)
        sb.headless = headless
        self._sessions[sid] = sb
        self._reserved_ports.add(port)
        return sb

    def list_sessions(self) -> List[Dict[str, Any]]:
        out = []
        for sid, sb in self._sessions.items():
            st = sb.get_state()
            out.append({
                "session_id": sid,
                "is_active": st.get("is_active"),
                "browser_mode": st.get("browser_mode"),
                "debug_port": st.get("debug_port"),
                "current_url": st.get("current_url"),
                "current_title": st.get("current_title"),
                "claimed_tab_id": st.get("claimed_tab_id"),
            })
        return out

    def close_session(self, session_id: str) -> Dict[str, Any]:
        sid = _safe_session_id(session_id)
        sb = self._sessions.pop(sid, None)
        if sb is None:
            return {"success": False, "error": f"No browser session '{sid}'"}
        try:
            sb.close()
        finally:
            self._reserved_ports.discard(sb.sandbox_port)
        return {"success": True, "closed": sid}

    def close_all(self) -> Dict[str, Any]:
        closed = []
        for sid in list(self._sessions.keys()):
            r = self.close_session(sid)
            if r.get("success"):
                closed.append(sid)
        return {"success": True, "closed": closed}

    async def run_parallel(
        self,
        jobs: List[Dict[str, Any]],
        close_after: bool = False,
    ) -> Dict[str, Any]:
        """Runs multiple browser step-runs concurrently across separate Chrome instances.
        Each job dict: {"session_id": "b1", "steps": [...], "owner": "...", "longform_session": "..."}."""
        from app.services.browser_run import run_browser_steps

        if not jobs:
            return {"success": False, "error": "No jobs provided"}

        t0 = time.time()

        async def _run_one(idx: int, job: Dict[str, Any]) -> Dict[str, Any]:
            sid = _safe_session_id(str(job.get("session_id") or f"worker_{idx + 1}"))
            sb = self.get_or_create(sid, headless=bool(job.get("headless", True)))
            jt0 = time.time()
            try:
                res = await run_browser_steps(
                    sb,
                    job.get("steps") or [],
                    owner=str(job.get("owner") or f"pool:{sid}"),
                    stop_on_failure=bool(job.get("stop_on_failure", True)),
                    max_recoveries=int(job.get("max_recoveries", 2)),
                    longform_session=job.get("longform_session"),
                )
                return {
                    "session_id": sid,
                    "port": sb.active_port(),
                    "elapsed_sec": round(time.time() - jt0, 2),
                    "result": res,
                }
            except Exception as e:
                return {
                    "session_id": sid,
                    "port": sb.active_port(),
                    "elapsed_sec": round(time.time() - jt0, 2),
                    "result": {"success": False, "status": "failed", "error": str(e)},
                }
            finally:
                if close_after:
                    self.close_session(sid)

        outcomes = await asyncio.gather(*(_run_one(i, j) for i, j in enumerate(jobs)))
        wall_sec = round(time.time() - t0, 2)
        sum_sec = round(sum(o["elapsed_sec"] for o in outcomes), 2)
        all_ok = all((o.get("result") or {}).get("status") == "completed" for o in outcomes)
        return {
            "success": all_ok,
            "browser_count": len(outcomes),
            "wall_sec": wall_sec,
            "sum_job_sec": sum_sec,
            "concurrency_ratio": round(sum_sec / wall_sec, 2) if wall_sec > 0 else 1.0,
            "outcomes": outcomes,
        }


browser_pool_service = BrowserPoolService()
