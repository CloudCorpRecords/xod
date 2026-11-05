// ==============================================================
// WORK ROUTER (Phase 1, simplicity redesign): the pure decisions
// behind the ONE composer and the status line. No DOM, no fetch:
// the same file loads in the page (window.WorkRouter) and in the
// node test harness (module.exports), so what is tested is what
// runs. The user never picks "task vs mission" any more; Xod
// decides here, from the words of the job itself.
//
// Rules (spec section 3, "Ask"):
//  - one step, nothing sensitive  -> a quick task, runs at once
//  - several steps                -> a mission (Xod plans it)
//  - big OR touching sending / money / deleting -> the plan is
//    shown in plain words before anything runs
// ==============================================================
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.WorkRouter = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  // Words that mean a job touches sending, money, or deleting.
  // A match never blocks the job; it only earns the plain-words
  // plan check before the job runs.
  const SENSITIVE = [
    [/\b(send|sends|sending|sent|email|e-mail|mail|message|text|dm|post|publish|share|forward|reply|submit|apply|sign up|signup)\b/i, "sending"],
    [/\b(pay|pays|paying|paid|buy|buys|buying|purchase|order|orders|charge|checkout|subscribe|donate|transfer|invoice|refund|book|reserve)\b|\$\s?\d/i, "money"],
    [/\b(delete|deletes|deleting|remove|removes|removing|erase|wipe|uninstall|trash|discard|destroy)\b/i, "deleting"],
  ];

  const ACTION_VERBS = [
    "sort", "create", "make", "build", "write", "find", "search", "check",
    "organise", "organize", "rename", "move", "copy", "download", "upload",
    "read", "summarise", "summarize", "compare", "fill", "open", "install",
    "fix", "update", "clean", "gather", "collect", "prepare", "draft",
    "schedule", "convert", "extract", "generate", "list", "review",
    "research", "count", "fetch", "print", "save", "set up", "setup",
  ];

  function sensitiveKinds(text) {
    const kinds = [];
    for (const [re, kind] of SENSITIVE) if (re.test(text)) kinds.push(kind);
    return kinds;
  }

  // A rough step count from the words alone: sequence markers
  // ("then", "after that", a numbered list) and how many different
  // things the job asks Xod to do.
  function estimateSteps(text) {
    const t = " " + String(text || "").toLowerCase() + " ";
    let steps = 1;
    const seq = (t.match(/\b(then|after that|afterwards|finally|next,|once .{0,30} done)\b/g) || []).length
      + (t.match(/(^|\n)\s*\d+[.)]\s/g) || []).length;
    steps += Math.min(seq, 3);
    const lower = String(text || "").toLowerCase();
    const verbs = ACTION_VERBS.filter((v) => new RegExp("\\b" + v + "\\b").test(lower)).length;
    if (verbs > 1) steps += Math.min(verbs - 1, 2);
    const sentences = (String(text || "").match(/[.!?]+(\s|$)/g) || []).length;
    if (sentences >= 3) steps += 1;
    return Math.min(steps, 6);
  }

  // The one routing decision. Returns:
  //   { kind: "task" | "mission", steps, sensitive: [kinds],
  //     big, needsConfirm }
  function classifyWorkRequest(text) {
    const clean = String(text || "").trim();
    if (!clean) return null;
    const sensitive = sensitiveKinds(clean);
    const steps = estimateSteps(clean);
    const kind = steps >= 2 ? "mission" : "task";
    const big = steps >= 3 || clean.length >= 220;
    return {
      kind,
      steps,
      sensitive,
      big,
      // The plan (or, for a one-step job, a plain summary) is shown
      // before running only when the job is big or sensitive.
      needsConfirm: big || sensitive.length > 0,
    };
  }

  // Why a job needs the plain-words check, in the user's words.
  function confirmReason(route) {
    if (!route) return "";
    if (route.sensitive.includes("sending")) return "This job sends something, so check the plan before it runs.";
    if (route.sensitive.includes("money")) return "This job involves money, so check the plan before it runs.";
    if (route.sensitive.includes("deleting")) return "This job deletes something, so check the plan before it runs.";
    return "This is a bigger job, so check the plan before it runs.";
  }

  // The header status line: one human sentence, always.
  function statusLineText({ needsYou = 0, working = 0 } = {}) {
    if (needsYou > 0) return `Needs you: ${needsYou}`;
    if (working === 1) return "Working on 1 thing.";
    if (working > 1) return `Working on ${working} things.`;
    return "All good.";
  }

  // Which Work-feed section a task / mission belongs in. "needs"
  // is always pinned first by the renderer.
  function taskBucket(status) {
    if (status === "waiting") return "needs";
    if (status === "working" || status === "queued") return "running";
    return "finished";
  }
  function missionBucket(state) {
    if (state === "paused") return "needs";
    if (state === "planning" || state === "running") return "running";
    return "finished";
  }

  // A finished job's headline: "Done." plus the first plain
  // sentence of what Xod reported, markdown stripped. Falls back
  // to the job's own words when there is no report.
  function plainOutcome(task) {
    const raw = String((task && task.result) || "")
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/[#>*_`\-]|\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\s+/g, " ")
      .trim();
    const first = (raw.match(/^[^.!?]{4,110}[.!?]/) || [raw.slice(0, 110)])[0].trim();
    if (first) return "Done. " + (first.endsWith(".") || first.endsWith("!") || first.endsWith("?") ? first : first + ".");
    const title = String((task && task.title) || "").trim();
    return title ? `Done: ${title}` : "Done.";
  }

  return { classifyWorkRequest, confirmReason, statusLineText, taskBucket, missionBucket, plainOutcome, estimateSteps };
});
