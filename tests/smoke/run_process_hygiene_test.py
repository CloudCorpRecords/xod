"""
Computer tool process hygiene: every child a computer tool spawns gets a deadline, capped output,
and guaranteed reaping (its whole process group) on completion, timeout, cancel, and task end.

The damaging shape from the eval runs: a model runs `find` (or a find that ends up reading a FIFO)
through computer_run_command, the shell is killed or the task is cancelled, and the find/cat child
survives as an orphan walking the home directory for hours. These checks plant a pathological
tree (a FIFO, an unreadable directory, a very deep chain), run the real SystemControlService,
and assert on the live process table: zero descendants of this test process after every case.

  python tests/smoke/run_process_hygiene_test.py
"""
import asyncio
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("XOD_SPEECH", "off")  # tests never reach the speakers (services/speech.py speech_off)
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="xod-hygiene-")
os.environ["XOD_DATA_DIR"] = os.path.join(TMP, "data")

from app.services import activity  # noqa: E402
from app.services.system_control import system_control_service as svc  # noqa: E402

failures, passed = [], 0


def check(name, ok, detail=""):
    global passed
    if ok:
        passed += 1
        print(f"  PASS {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}  {detail}")


def descendants():
    """All live descendants of this test process, from the real process table."""
    proc = subprocess.Popen(["ps", "-axo", "pid=,ppid=,command="], stdout=subprocess.PIPE, text=True)
    out = proc.communicate()[0]
    kids = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            if int(parts[0]) == proc.pid:
                continue  # the scanning ps is itself a child while it runs
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, stack = [], list(kids.get(os.getpid(), []))
    while stack:
        pid = stack.pop()
        found.append(pid)
        stack.extend(kids.get(pid, []))
    return found


def cleanup_leftovers():
    """Test-rig cleanup only: on a failing (unfixed) tree the orphan is real, and this suite must
    not leave it walking the machine after the failure has been recorded."""
    for pid in descendants():
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.3)


def plant_tree():
    root = Path(TMP) / "tree"
    deep = root
    for i in range(60):
        deep = deep / f"d{i:02d}"
    deep.mkdir(parents=True)
    (deep / "deep-note.txt").write_text("deep\n")
    (root / "plain.txt").write_text("plain\n")
    os.mkfifo(root / "pipe.txt")
    locked = root / "locked"
    locked.mkdir()
    (locked / "secret.txt").write_text("secret\n")
    os.chmod(locked, 0)
    return root


async def main():
    root = plant_tree()
    fifo = root / "pipe.txt"
    q = shlex.quote

    # 1. A normal find over the pathological tree completes and leaves nothing behind.
    before = set(descendants())
    r = await svc.run_command(f"find {q(str(root))} -type f -name '*.txt'", timeout_sec=15)
    # find exits nonzero on the unreadable directory (expected); what matters is
    # that it finished at all, inside the deadline, with the readable hits.
    check("find over planted tree completes", r.get("status") in ("completed", "failed"), str(r)[:200])
    check("find sees the deep file", "deep-note.txt" in (r.get("stdout") or ""))
    check("no children after a completed find", set(descendants()) == before,
          f"left={sorted(set(descendants()) - before)}")
    cleanup_leftovers()

    # 2. A find whose output reader blocks on the FIFO times out inside its deadline,
    #    returns a named timeout, and leaves zero children (the re-run orphan shape).
    cmd = f"find {q(str(root))} -type f -print; cat {q(str(fifo))}"
    t0 = time.time()
    r = await svc.run_command(cmd, timeout_sec=2)
    elapsed = time.time() - t0
    await asyncio.sleep(0.5)
    check("hung find returns inside its deadline", elapsed < 12, f"elapsed={elapsed:.1f}")
    check("hung find returns a named timeout", r.get("status") == "timed_out"
          and "timed out" in (r.get("stderr") or ""), str(r)[:200])
    check("no children after a timed-out find", not descendants(), f"left={descendants()}")
    cleanup_leftovers()

    # 3. Output is capped in bytes and the truncation is labeled.
    r = await svc.run_command(f"{q(sys.executable)} -c 'print(chr(120) * 500000)'", timeout_sec=15)
    check("huge output is capped and labeled", len(r.get("stdout") or "") <= 70000
          and "Truncated" in (r.get("stdout") or ""), f"len={len(r.get('stdout') or '')}")
    check("no children after a capped command", not descendants(), f"left={descendants()}")
    cleanup_leftovers()

    # 4. Cancelling the task mid-find reaps the whole process group.
    task = asyncio.create_task(svc.run_command(f"cat {q(str(fifo))}", timeout_sec=60))
    await asyncio.sleep(0.7)
    check("find child is alive while the task runs", len(descendants()) >= 1, f"seen={descendants()}")
    task.cancel()
    cancelled = False
    try:
        await asyncio.wait_for(task, timeout=10)
    except asyncio.CancelledError:
        cancelled = True
    except asyncio.TimeoutError:
        pass
    await asyncio.sleep(0.5)
    check("cancelled task raises CancelledError", cancelled)
    check("no children after a cancelled find", not descendants(), f"left={descendants()}")
    cleanup_leftovers()

    # 5. Task-end sweep: a task that ends while its find is still running leaves zero
    #    children after the sweep, and the sweep only takes that task's own children.
    activity.origin.set("task:hygiene-test")
    task = asyncio.create_task(svc.run_command(f"cat {q(str(fifo))}", timeout_sec=60))
    await asyncio.sleep(0.7)
    sweep = getattr(svc, "reap_tool_children", None)
    check("service exposes a task-end sweep", sweep is not None)
    if sweep is not None:
        other = await sweep(origin="task:someone-else")
        check("sweep for another task reaps nothing of this one", other == 0 and len(descendants()) >= 1,
              f"other={other} seen={descendants()}")
        reaped = await sweep(origin="task:hygiene-test")
        check("task-end sweep reaps the running find", reaped >= 1, f"reaped={reaped}")
        try:
            await asyncio.wait_for(task, timeout=10)
        except Exception:
            pass
    await asyncio.sleep(0.5)
    check("no children after the task-end sweep", not descendants(), f"left={descendants()}")
    cleanup_leftovers()

    # 6. computer_find_files (mdfind) returns bounded and leaves nothing behind.
    t0 = time.time()
    r = await svc.find_files("deep-note", root_dir=str(root))
    check("find_files returns inside its deadline", time.time() - t0 < 12 and isinstance(r, dict), str(r)[:200])
    check("no children after find_files", not descendants(), f"left={descendants()}")
    cleanup_leftovers()

    try:
        os.chmod(root / "locked", 0o700)
    except OSError:
        pass
    print(f"\n{passed} passed, {len(failures)} failed")
    if failures:
        print("failed:", ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
