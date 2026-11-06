"""
Silent self-maintenance (Phase 5 of the simplicity redesign).

Principle 6 of the redesign: the app heals and updates itself silently, and
reports outcomes only. This module is the routing layer for that:

  - Updates are automatic by default (services/updates.py holds the policy).
    A newer version is downloaded and installed by the existing updater
    machinery (electron/updater.js installs on quit; the browser build links
    to the release page) WITHOUT prompting. When Xod comes back on a new
    version, note_update_if_changed() reports it as an ordinary result.
  - Health faults Xod can already repair itself (every fix in
    services/doctor.py already exists as a code path, plus the Phase 3
    post-office repair) are attempted silently FIRST. The human is pulled
    in only when a repair fails, and then as ONE calm Needs-you card in
    the Work feed (one sentence, plus a Fix it button where a manual path
    exists). A human is pulled in without a repair attempt only for
    genuine decisions (money, sending, deleting, connecting a new agent) -
    none of the doctor's checks are those, so every doctor fault gets a
    repair attempt first.
  - Outcomes are reported as ordinary result cards in Work's Finished
    bucket (a finished task whose title IS the outcome sentence, written
    as an outcome, not a step log) and as outcome entries in Work -
    History (services/activity.py), where Details holds the technical
    record. Failed-and-unrecovered maintenance lands in Needs you, never
    in Finished.
  - Nothing here runs for scratch/proving work: outcome cards are stamped
    origin="self" (a real, non-scratch origin - see task_queue.is_scratch_job)
    and are only written by the real maintenance paths below, never by
    test/proving harnesses (they call the pieces with stubs and throwaway
    data dirs).

The background loop started from main.py runs the health cycle on the
doctor's own cadence and the update bookkeeping once an hour. Every piece
takes its collaborators as arguments so tests can stub them.
"""
import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("xod.self_maintenance")

ORIGIN = "self"                    # tasks stamped with this origin are real (not scratch)
ACTIVITY_ORIGIN = "self-maintenance"
HEALTH_EVERY_SEC = 300             # the doctor's own cadence (doctor.CHECK_EVERY_SEC)

# Calm one-sentence wording per doctor check: what the human reads when a
# repair genuinely failed (Needs you) and what the result card says when
# Xod fixed it on its own (Finished). No ports, no processes, no jargon.
NEEDS_YOU_LINE = {
    "chat_model": "Xod's chat model needs a hand.",
    "memory": "This computer is low on memory and Xod couldn't free any.",
    "disk": "This computer is nearly out of disk space.",
    "browser": "Xod's browser needs a hand.",
    "media": "Xod's image and video tools need a hand.",
    "tasks": "Some of Xod's jobs are stuck and Xod couldn't unstick them.",
    "keys": "Xod needs a web search key to search well.",
    "failures": "Something in Xod keeps failing.",
    "messages": "Messages are paused.",
}
FIXED_LINE = {
    "chat_model": "Xod fixed its chat model on its own.",
    "memory": "Xod freed memory on its own.",
    "browser": "Xod fixed its browser on its own.",
    "media": "Xod fixed its image and video tools on its own.",
    "tasks": "Xod unstuck its jobs on its own.",
    "messages": "Xod fixed its messages on its own.",
}


# ---------------------------------------------------------------- outcomes

def record_outcome(sentence: str, detail: str = "", ok: bool = True) -> Optional[str]:
    """Writes one self-maintenance outcome: a Finished result card in Work
    (a finished task titled with the outcome sentence) plus a History
    (Activity) entry whose detail holds the technical record. Returns the
    task id, or None if recording failed (maintenance never crashes on
    its own bookkeeping)."""
    try:
        from app.services.task_queue import task_queue
        task = task_queue.record_finished(sentence, sentence, origin=ORIGIN)
        try:
            from app.services import activity
            activity.record(sentence, detail or sentence, ok=ok,
                            origin=ACTIVITY_ORIGIN, tool="self_maintenance")
        except Exception as e:
            logger.warning(f"self-maintenance: couldn't write the history entry: {e}")
        return task["id"] if task else None
    except Exception as e:
        logger.warning(f"self-maintenance: couldn't record an outcome card: {e}")
        return None


def record_needs_you(key: str, sentence: str, detail: str = "",
                     fix: Optional[str] = None) -> Optional[str]:
    """Lands ONE calm Needs-you card in Work for an unrecovered fault.
    Deduped by key: a fault that is already waiting never stacks a second
    card. `fix` names the manual path the card's Fix it button runs
    ("doctor:<fix id>" or "mail:repair"); None means there is no manual
    path and the card is information only. Also leaves a History entry so
    the pull-in itself is visible after the fact."""
    try:
        from app.services.task_queue import task_queue
        task = task_queue.record_waiting_maintenance(key, sentence, detail, fix)
        if task and task.get("_created"):
            try:
                from app.services import activity
                activity.record(sentence, detail or sentence, ok=False,
                                origin=ACTIVITY_ORIGIN, tool="self_maintenance")
            except Exception as e:
                logger.warning(f"self-maintenance: couldn't write the history entry: {e}")
        return task["id"] if task else None
    except Exception as e:
        logger.warning(f"self-maintenance: couldn't record a Needs-you card: {e}")
        return None


def resolve_needs_you(key: str, sentence: str) -> bool:
    """A fault that later healed (or was fixed from its card) leaves Needs
    you: its waiting card is completed with the outcome sentence."""
    try:
        from app.services.task_queue import task_queue
        return task_queue.resolve_waiting_maintenance(key, sentence)
    except Exception as e:
        logger.warning(f"self-maintenance: couldn't resolve Needs-you {key}: {e}")
        return False


# ---------------------------------------------------------------- updates

def note_update_if_changed(current_version: Optional[str] = None) -> Optional[str]:
    """Called at startup. If the version Xod last ran differs from the one
    running now, an update landed while Xod was away (the updater installs
    on quit): report it as an ordinary result card, "Xod updated itself
    last night." The very first run only stamps the version - no card, so
    installs and proving runs never invent an update story."""
    try:
        from app.services.storage import get_setting, set_setting
        from app.version import VERSION
        current = current_version or VERSION
        seen = get_setting("app_version_seen", "")
        if seen == current:
            return None
        set_setting("app_version_seen", current)
        if not seen:
            return None
        return record_outcome("Xod updated itself last night.",
                              f"Updated from {seen} to {current} while Xod was closed.")
    except Exception as e:
        logger.warning(f"self-maintenance: update bookkeeping failed: {e}")
        return None


async def run_update_cycle(status_fn: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
                           install_fn: Optional[Callable[[Dict[str, Any]], Awaitable[Any]]] = None
                           ) -> Dict[str, Any]:
    """One automatic-update pass, with the update source stubbable.

    Policy off -> nothing is checked or installed (the off setting is
    respected exactly). No newer version -> nothing. A newer version ->
    install_fn (in the desktop app, the existing updater machinery; in
    tests, a stub) runs WITHOUT any prompt or Needs-you card, the
    last-updated stamp is saved, and the outcome is recorded as a
    Finished result card. If installing genuinely fails, the failure -
    and only the failure - becomes one calm Needs-you card."""
    from app.services import updates as updates_svc
    if status_fn is None:
        status_fn = updates_svc.status
    if not updates_svc.auto_enabled():
        return {"state": "off"}
    st = await status_fn()
    if not st or not st.get("available"):
        return {"state": "uptodate", "current": (st or {}).get("current")}
    version = st.get("latest") or "a new version"
    if install_fn is None:
        # No installer on this surface (the browser build): the desktop
        # updater owns installing. Nothing to prompt about either way.
        return {"state": "available", "latest": version}
    try:
        await install_fn(st)
    except Exception as e:
        record_needs_you("update", "Xod couldn't update itself.",
                         f"The update to {version} failed: {e}", fix=None)
        return {"state": "failed", "latest": version, "error": str(e)}
    updates_svc.mark_updated(version)
    record_outcome("Xod updated itself last night.",
                   f"Updated to {version} automatically; no prompt was shown.")
    return {"state": "updated", "latest": version}


# ---------------------------------------------------------------- health

async def run_health_cycle(doctor_obj: Any = None,
                           mail_status_fn: Optional[Callable[[], Dict[str, Any]]] = None,
                           mail_repair_fn: Optional[Callable[[], Awaitable[Dict[str, Any]]]] = None
                           ) -> Dict[str, Any]:
    """One self-repair pass over the existing health machinery.

    For every failing doctor check that carries a fix, the fix (which
    already exists in doctor.fix) is attempted silently. Repaired ->
    outcome card + History entry, no Needs-you card. Still failing after
    the attempt -> exactly one calm Needs-you card (deduped), with Fix it
    wired to the same fix for a manual retry. Failing checks with no fix
    are genuine human territory (disk space, a missing key): one calm
    Needs-you card, no button invented. Warning-level checks never
    surface. The Phase 3 post office joins the same routing: paused ->
    one Needs-you card whose Fix it is the existing repair path."""
    if doctor_obj is None:
        from app.services.doctor import doctor as doctor_obj
    if mail_status_fn is None:
        from app.services import agent_mail
        mail_status_fn = agent_mail.status
    if mail_repair_fn is None:
        from app.services import agent_mail
        mail_repair_fn = agent_mail.repair_home_post

    repaired: List[str] = []
    surfaced: List[str] = []
    report = await doctor_obj.check()
    checks = {c["name"]: c for c in report.get("checks", [])}
    failing = [c for c in checks.values() if c.get("level") == "fail"]
    for c in failing:
        name, fix = c["name"], c.get("fix")
        if fix:
            res = await doctor_obj.fix(fix)
            after = await doctor_obj.check()
            now = {x["name"]: x for x in after.get("checks", [])}.get(name, {})
            if res.get("ok") and now.get("ok"):
                resolve_needs_you(f"health:{name}", FIXED_LINE.get(name, "Xod fixed this on its own."))
                record_outcome(FIXED_LINE.get(name, "Xod fixed a problem on its own."),
                               f"Health check '{name}' was failing ({c.get('detail', '')}). "
                               f"Xod repaired it itself: {res.get('detail', '')}")
                repaired.append(name)
                continue
            record_needs_you(f"health:{name}",
                             NEEDS_YOU_LINE.get(name, "Something in Xod needs a hand."),
                             f"Health check '{name}' is failing ({c.get('detail', '')}). "
                             f"Xod tried its own repair ({fix}) first: {res.get('detail', '')}",
                             fix=f"doctor:{fix}")
            surfaced.append(name)
        else:
            record_needs_you(f"health:{name}",
                             NEEDS_YOU_LINE.get(name, "Something in Xod needs a hand."),
                             f"Health check '{name}' is failing ({c.get('detail', '')}). "
                             "Xod has no repair of its own for this one.",
                             fix=None)
            surfaced.append(name)
    # Checks that healed on their own since a card was raised leave Needs you.
    for name, c in checks.items():
        if c.get("ok"):
            resolve_needs_you(f"health:{name}", FIXED_LINE.get(name, "Xod fixed this on its own."))

    # Messages (Phase 3's self-healing post office, same routing): the mail
    # loop already attempts silent recovery every cycle; "paused" is the
    # state where its own repairs genuinely failed.
    try:
        home = (mail_status_fn() or {}).get("home") or {}
        if home.get("paused"):
            record_needs_you("health:messages", NEEDS_YOU_LINE["messages"],
                             f"The post office could not restart itself ({home.get('last_error') or 'unknown error'}). "
                             "Xod already tried restarting it on its own.",
                             fix="mail:repair")
            surfaced.append("messages")
        elif home.get("running"):
            resolve_needs_you("health:messages", FIXED_LINE["messages"])
    except Exception as e:
        logger.warning(f"self-maintenance: mail health routing failed: {e}")
    return {"repaired": repaired, "surfaced": surfaced}


async def run_manual_fix(task_id: str) -> Dict[str, Any]:
    """The Needs-you card's Fix it button: runs the manual path the card
    names (an existing doctor fix, or the Phase 3 post-office repair).
    On success the card completes as the outcome; on failure it stays in
    Needs you. No new repair powers are invented here."""
    from app.services.task_queue import task_queue
    task = task_queue.get(task_id)
    if not task or task.get("status") != "waiting" or task.get("waiting_kind") != "maintenance":
        return {"ok": False, "detail": "That card isn't waiting any more."}
    detail_in = task.get("waiting_detail") or {}
    fix = detail_in.get("fix") or ""
    key = detail_in.get("key") or ""
    name = key.split(":", 1)[-1] if ":" in key else key
    if fix.startswith("doctor:"):
        from app.services.doctor import doctor
        res = await doctor.fix(fix.split(":", 1)[1])
        ok, detail = bool(res.get("ok")), res.get("detail", "")
    elif fix == "mail:repair":
        from app.services import agent_mail
        res = await agent_mail.repair_home_post()
        ok, detail = bool(res.get("success")), res.get("error") or "Messages are running again."
        if ok:
            try:
                from app.services import activity
                activity.record(FIXED_LINE["messages"],
                                "The post office was restarted from the Fix it button after "
                                "automatic recovery had paused.", ok=True,
                                origin=ACTIVITY_ORIGIN, tool="self_maintenance")
            except Exception:
                pass
    else:
        return {"ok": False, "detail": "There's no manual fix for this one."}
    if ok:
        sentence = ("Messages are working again." if fix == "mail:repair"
                    else FIXED_LINE.get(name, "Xod fixed this on its own."))
        resolve_needs_you(key, sentence)
        return {"ok": True, "detail": detail}
    return {"ok": False, "detail": detail or "Xod still couldn't fix it."}


# ---------------------------------------------------------------- background

_task: Optional[asyncio.Task] = None


async def _loop() -> None:
    await asyncio.sleep(60)          # let the app finish starting (doctor waits 20 s)
    note_update_if_changed()
    while True:
        try:
            await run_health_cycle()
        except Exception as e:
            logger.warning(f"self-maintenance health cycle failed: {e}")
        try:
            await run_update_cycle()
        except Exception as e:
            logger.warning(f"self-maintenance update cycle failed: {e}")
        await asyncio.sleep(HEALTH_EVERY_SEC)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())
