// The review half of Xod's activity timer (services/liveness.py is the
// timer half, inside the backend). The design, settled 2026-11-10: nothing
// here probes on a loop. The backend's own activity resets the timer;
// this module reads the timer's ledger, and only when the ledger has
// been stale past the window WITH work in flight (or activity that
// stopped very recently) does it run a review: read the state, check
// the process, capture the evidence, and decide. A restart happens
// only as that review's decision, through the shell's own stop/start
// path, under a restart budget with backoff and a hold, because the
// 2026-11-08 backend watchdog crash-looped a Mac to load average 304
// and a respin loop is a worse failure than the wedge.
//
// Why the review lives in the shell and not in the backend: the AGE-7
// wedge parked the backend process's event loop (its main thread sat
// in open()) and its worker threads on locks. The orchestrator, the
// task queue and every in-process watchdog share that process, so a
// review in there parks with the wedge it is meant to catch. The
// shell is the nearest always-on part of Xod that survives: a separate
// process, and already the backend's supervisor (it spawns it, stops
// it, and restarts it). Pure logic over injected io, like mode.js, so
// `node --test electron/liveness.test.js` drives every path.

const DEFAULTS = {
  stallAfterSec: 45,        // the stall window: nothing through in ~45 s with work in flight earns a review
  recentActivitySec: 165,   // no work in flight, but activity that stopped inside this window is still a stall, not idle
  standDownGraceSec: 120,   // a healthy review stands down this stall episode for this long
  maxRestarts: 3,           // restart budget per window (the crash-loop lesson)
  budgetWindowSec: 600,     // 10 minutes
  backoffSec: [60, 120, 240], // minimum gap before the 2nd, 3rd, ... restart inside the window
  recoveryTimeoutSec: 180,  // a restart that has not answered hello by now is recorded as failed
};

function lastProgressAt(ledger) {
  let latest = Number(ledger && ledger.last_activity_at) || 0;
  const inFlight = (ledger && ledger.in_flight) || {};
  for (const item of Object.values(inFlight)) {
    latest = Math.max(latest, Number(item && item.last_progress_at) || 0);
  }
  return latest;
}

function episodeKey(ledger) {
  return `${ledger && ledger.boot_at}:${lastProgressAt(ledger)}`;
}

class LivenessReview {
  // io: {
  //   now() -> seconds, readLedger() -> object|null,
  //   expectedPid() -> number|null (the backend this shell supervises),
  //   processAlive(pid) -> bool, helloAnswers() -> Promise<bool>,
  //   captureEvidence(ctx) -> Promise<string> (incident dir; must not throw),
  //   recordIncident(dir, record) -> void,
  //   restart(ctx) -> Promise<bool>, log(msg) -> void,
  //   loadState() -> object, saveState(state) -> void,
  // }
  constructor(io, opts = {}) {
    this.io = io;
    this.opts = { ...DEFAULTS, ...opts };
    this.state = { restartTimes: [], held: false, pendingRecovery: null,
                   standDownUntil: 0, standDownEpisode: "", evidenceEpisode: "",
                   lastRecoverySec: null, ...(io.loadState() || {}) };
  }

  _save() {
    try { this.io.saveState(this.state); } catch (e) { /* state is supervision bookkeeping, never fatal */ }
  }

  _prune(now) {
    const cutoff = now - this.opts.budgetWindowSec;
    this.state.restartTimes = this.state.restartTimes.filter((t) => t > cutoff);
    if (this.state.held && this.state.restartTimes.length === 0) {
      this.state.held = false;   // the window fully cleared; supervision resumes, loudly logged by the caller path
      this.io.log("liveness: restart budget window cleared, hold released");
    }
  }

  async tick() {
    const now = this.io.now();
    this._prune(now);

    // A restart is outstanding: the only question is whether hello is back.
    if (this.state.pendingRecovery) {
      if (await this.io.helloAnswers()) {
        const took = now - this.state.pendingRecovery.at;
        this.state.lastRecoverySec = took;
        this.io.recordIncident(this.state.pendingRecovery.dir,
          { recovered_at: now, time_to_recover_sec: took });
        this.io.log(`liveness: backend answered again, recovered in ${Math.round(took)}s`);
        this.state.pendingRecovery = null;
        this.state.held = false;
        this._save();
        return "recovered";
      }
      if (now - this.state.pendingRecovery.at > this.opts.recoveryTimeoutSec) {
        this.io.recordIncident(this.state.pendingRecovery.dir,
          { recovery_failed_at: now });
        this.io.log("liveness: restart issued but the backend never answered; recorded as a failed recovery");
        this.state.pendingRecovery = null;
        this._save();
        return "recovery_failed";
      }
      return "recovering";
    }

    const ledger = this.io.readLedger();
    if (!ledger) return "quiet";   // no backend has written a ledger yet (boot, or a pre-timer backend)
    const expected = this.io.expectedPid();
    if (expected && ledger.pid && Number(ledger.pid) !== Number(expected)) {
      return "quiet";   // the ledger belongs to an older backend instance, not the one this shell supervises
    }

    const inFlight = Object.keys(ledger.in_flight || {});
    const staleFor = now - lastProgressAt(ledger);
    if (staleFor < this.opts.stallAfterSec) {
      return inFlight.length ? "watching" : "quiet";
    }
    if (inFlight.length === 0 && now - (Number(ledger.last_activity_at) || 0) > this.opts.recentActivitySec) {
      return "quiet";   // idle for minutes with nothing in flight: a quiet machine, not a stall
    }
    if (this.state.held) {
      // Holding means no more restarts, not no more watching: if the
      // backend came back on its own, say so and clear the hold.
      if (await this.io.helloAnswers()) {
        this.state.held = false;
        this._save();
        this.io.log("liveness: backend answered while held; hold cleared");
        return "recovered";
      }
      return "held";
    }
    const key = episodeKey(ledger);
    if (this.state.standDownEpisode === key && now < this.state.standDownUntil) {
      return "standing_down";
    }
    return this.review(ledger, staleFor, key);
  }

  async review(ledger, staleFor, key) {
    const now = this.io.now();
    const pid = ledger.pid || null;
    const alive = pid ? !!this.io.processAlive(pid) : false;
    const hello = alive ? await this.io.helloAnswers() : false;
    const base = {
      at: now, pid, stale_for_sec: Math.round(staleFor),
      in_flight: ledger.in_flight || {},
      last_activity_at: ledger.last_activity_at || null,
      last_activity_kind: ledger.last_activity_kind || "",
      process_alive: alive, hello_answered: hello,
    };

    if (hello) {
      // Diagnosis: the server answers. A long quiet step (a build, a
      // download) is not a wedge. Stand this episode down and say so.
      this.state.standDownEpisode = key;
      this.state.standDownUntil = now + this.opts.standDownGraceSec;
      this._save();
      this.io.log("liveness: review found the backend answering (long quiet work, not a wedge); standing down");
      return "review_healthy";
    }

    // Evidence first, restart second: once the process is gone, the
    // stack that explains the wedge is gone with it. A capture failure
    // must never block the restart, so captureEvidence is contracted
    // not to throw, and its result is only ever a path or null.
    let dir = null;
    if (this.state.evidenceEpisode !== key) {
      try { dir = await this.io.captureEvidence({ ...base }); } catch (e) { dir = null; }
      this.state.evidenceEpisode = key;
      this._save();
    }

    const times = this.state.restartTimes;
    if (times.length >= this.opts.maxRestarts) {
      this.state.held = true;
      this._save();
      this.io.log(`liveness: HOLD. ${times.length} restarts inside ${this.opts.budgetWindowSec}s did not keep the backend up; ` +
        "no further restarts until the window clears or the backend answers again. A human should look.");
      if (dir) this.io.recordIncident(dir, { ...base, decision: "hold" });
      return "held";
    }
    if (times.length > 0) {
      const gap = this.opts.backoffSec[Math.min(times.length - 1, this.opts.backoffSec.length - 1)];
      if (now - times[times.length - 1] < gap) {
        if (dir) this.io.recordIncident(dir, { ...base, decision: "backoff_wait" });
        return "backoff";
      }
    }

    const issued = await this.io.restart({ pid, alive, staleFor });
    if (dir) this.io.recordIncident(dir, { ...base, decision: issued ? "restart" : "restart_failed" });
    if (!issued) {
      this.io.log("liveness: review decided to restart but the restart action failed; recorded, will re-review");
      return "restart_failed";
    }
    this.state.restartTimes.push(now);
    this.state.pendingRecovery = { at: now, dir };
    this._save();
    this.io.log(`liveness: review found the backend ${alive ? "wedged (alive, hello silent)" : "dead"}; restarted it ` +
      `(${this.state.restartTimes.length} of ${this.opts.maxRestarts} in the budget window)`);
    return "restarted";
  }
}

module.exports = { LivenessReview, DEFAULTS, lastProgressAt, episodeKey };
